# Phase 31 — Mini control app (31a read-only dashboard, 31b guarded actions)

**Status:** implemented locally on 2026-10-20 (31a and 31b; branch `feat/phase-31-control-app`, no commit or deployment). Design proposal 2026-10-12; locked 2026-10-15 (web app, LAN-only, HTTP first, Testing-mode OAuth, roles, English default) and 2026-10-20 (dependencies, roles per route, re-authorization as the 31b acceptance test, installer fix). Mini, router and Google Cloud actions remain separately authorized operator steps; see "Implementation notes (2026-10-20)" below.
**Depends on:**
- [Phase 30](phase-30-status-channel.md): status channel, `status_snapshot.py`, job run records. 31a can start with local readers if 30c lags.
- ADR 0006 (stable IDs, atomic stores), ADR 0009 (scheduled-post reconciliation), ADR 0013 (LaunchAgents, FileVault).
- Workshop note `cec-vivisystem-scratch/design-status-channel-and-control-app.md` (untracked) and mockup `control-app-mockup.html` beside it.

**ADRs at implementation:** 0015, form factor, network exposure, authentication and dependencies (31a). 0016, Google re-authorization flow (31b).
**Non-goals:**
- Calendar create/update/delete from the app.
- Triggering or rerunning scheduled jobs.
- Automatic restarts or restarts driven by health state.
- Public internet exposure.
- Native SwiftUI or menubar clients.
- Showing or editing secret values in the browser.
- Important-date edit/delete.

Inherits the ground rules (notably 6, 8, 9, 13 and 15), the unit-testing standard, and the logging/retention and resilience standards.

## Intent

Give both parents one calm, phone-first place on the Mac Mini to see whether the family bot is healthy and to fix the common problems. The problems: the listener is down, the system channel is wrong, Google access is expiring, and someone wants to see what is coming up. Existing contracts stay intact. The app is read-only first. Each later action is confirmed, rate-limited, audited and announced in the status channel.

## Proposed decisions (Carter confirms before lock)

1. **Form factor:** a local web app. Starlette, Jinja2, uvicorn and python-multipart, as an optional `control` extra so the listener's environment is unchanged. HTML is rendered on the server and pages work without JavaScript. htmx is optional and vendored if used (no CDN). Rejected: SwiftUI (two codebases, Apple distribution, no Linux tests) and a menubar app (Mini console only).
2. **Network (decided 2026-10-15: LAN-only, no Tailscale):** uvicorn binds the Mini's home-LAN address only (`CEC_CONTROL_BIND`, for example `192.168.x.y:8730`), never `0.0.0.0` and never a public interface. The app checks that the client IP is in an allowlisted private subnet (`CEC_CONTROL_ALLOWED_NETS`, default: the Mini's own /24) and rejects other IPs with 403. No router port forwarding. **Transport (locked 2026-10-15): HTTP on the home LAN first.** Session cookies are then not `Secure`, which is an accepted risk recorded in ADR 0015. Mitigations: IP allowlist, sign-in, `SameSite=Strict`, `HttpOnly`, and short re-entry for sensitive actions. **HTTPS later (optional):** an mkcert certificate trusted on both iPhones, or Tailscale. Access from outside the home is out of scope; Tailscale can be added later without changing the app (bind to loopback plus `tailscale serve`). The Mini should have a DHCP reservation and a Bonjour name (`mac-mini.local`).
3. **Accounts:** two accounts created by CLI on the Mini (no web sign-up). Roles (**approved by Carter 2026-10-15**):
   - *admin* (Carter): everything, including changing channels and restarting the listener.
   - *member* (wife): view status, view events and important dates, **run Google re-authorization** and "Test connection".
   - The member cannot change channels, restart the listener or manage users.
4. **Language (decided 2026-10-15):** both accounts default to **English, ASD-STE100 style, short and clear**. The `zh-Hant-HK` catalog stays as an option in the language switch (Traditional only) and does not block release.
5. **Visuals:** tokens, type and components from the design note §8. WCAG 2.2 AA in light and dark.
6. **Google re-authorization (31b):** the existing Desktop OAuth client with a loopback redirect and "paste the address" completion, plus PKCE and state. **Decided 2026-10-15:** the consent screen stays in *Testing*. The first real re-authorization after the 2026-10-15 expiry is done **through this flow as its acceptance test** (the helper script is not used), so the refresh token expires every 7 days. This makes re-authorization a weekly task for both parents, and the flow must be fast and simple on an iPhone: 2 taps to start, 1 paste to finish, and a clear expiry banner from 2 days before. The Overview banner and the status channel show "expired" if no one re-authorizes in time. On 2026-10-15 the token had already expired (`days_left=-10`, 07:00 recap `RefreshError`), which shows why this flow matters.

## Architecture

| Item | Design |
| --- | --- |
| Package | `src/cec_vivisystem/control/` (`app.py`, `auth.py`, `audit.py`, `envfile.py`, `launchd.py`, `oauth_google.py`, `copy/*.json`, `templates/`, `static/`); status reads from `cec_vivisystem.status_snapshot` |
| Factory | `create_app(config, clock, stores, runner, poster, calendar_client, http)`; every I/O dependency is injectable for offline tests |
| Entry point | `python -m cec_vivisystem.control serve` (uvicorn on `CEC_CONTROL_BIND`, default `127.0.0.1:8730`); `python -m cec_vivisystem.control adduser <name> --role admin\|member` (prompts for the passphrase) |
| LaunchAgent | `deploy/launchd/com.cec.vivisystem.control.plist.template`: `RunAtLoad`, `KeepAlive`, `ThrottleInterval` 30, stdout `/dev/null`, stderr `~/Library/Logs/cec-vivisystem/control.err.log`, `TZ=Asia/Hong_Kong`, `uv run --frozen --extra control python -m cec_vivisystem.control serve`. The installer renders it with the others; it never kickstarts it. |
| Reads | heartbeat, alert state, samples, job records, delivery markers, important dates (class F, read-only), `.env` through `dotenv_values` (secrets become presence flags only), `launchctl print` (read-only, macOS only) |
| Google | `list_calendar_events` for the next 14 days with a 60-s in-memory cache (no mirror, class G); `probe_google_client` for "Test connection" (31b) |
| Writes | 31a: audit only. 31b: allowlisted `.env` keys (with backup), Google token keys, `launchctl kickstart -k` for the listener only, status-channel notices. |
| Isolation | The listener and the jobs never import or call the app; a test asserts that importing `cec_vivisystem.listener` does not import Starlette. The app is not an orchestrator: if it is down, nothing else changes. |

## Screens

| ID | Screen | 31a | 31b |
| --- | --- | --- | --- |
| S0 | Sign in | yes | — |
| S1 | Overview: banner, status cards (listener, Slack connection, 07:00 recap, 10:00 dates, Google access, alerts), next 3 days, activity | read-only | action links enabled |
| S2 | Listener: state, process, heartbeat, 24-h timeline, events, alert policy | read-only | Restart with confirmation |
| S3 | Jobs: 07:00, 10:00, checker, digest; last run; pending deliveries with the ADR 0009 note | read-only (never a run button) | — |
| S4 | Calendar: next 14 days grouped by HKT day; all-day and exclusive-end semantics as in `format_recap` | read-only | — |
| S5 | Important dates by next occurrence; how to add in Slack | read-only | — |
| S6 | Slack channels: plans, life notes, system status (ID, bot membership if scopes allow) | read-only | Edit with validation, test message, restart prompt |
| S7 | Google access: expiry date and days left, last test | read-only | Test connection; re-authorize |
| S8 | Activity: audit plus alert history | yes | — |
| S9 | Confirm dialogs, 401/403/404/500, CSRF, offline states | yes | yes |

Wireframes and STE copy (EN + 繁中) are in the design note §6–§7. The mockup shows S1 in light and dark.

## Security requirements (both slices)

- **Network:** bind the LAN address only; client-IP allowlist of private subnets; Host allowlist (`mac-mini.local`, the LAN IP, `127.0.0.1`, `localhost`). HTTP on the LAN first (accepted risk, ADR 0015); HTTPS optional later. No port forwarding. `Cache-Control: no-store`.
- **Roles:** enforced on the server for each route. The member gets 403 for channel edits, restart and user management (test A11).
- **Authentication:**
  - stdlib `hashlib.scrypt`; passphrases of at least 12 characters
  - 5 failures lock the account and the source IP for 15 min; generic errors
  - in-memory sessions with 256-bit IDs in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` and the `__Host-` prefix only when HTTPS is enabled); idle 7 days, absolute 30 days
  - the password is required again for sensitive actions when the last entry is older than 15 min
- **Requests:** a per-session CSRF token on every POST plus an `Origin` check. GET never changes state.
- **Headers:** CSP `default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'`; `Referrer-Policy: no-referrer`; `X-Content-Type-Options: nosniff`.
- **Secrets:**
  - shown as "Set" / "Not set" only (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `XAI_API_KEY`, `LLM_API_KEY`)
  - never partially masked, logged, audited, or passed to templates
  - OAuth codes and session IDs are never logged
- **Calendar gate:** the control package never references `write_calendar_create`, `create_event` or `resolve_confirmation`, and a static test enforces it. A future write (31c) must create a normal pending confirmation and wait for an explicit "yes".
- **Restart:** human click plus confirmation screen; at most one per 5 min; fixed argv `["launchctl", "kickstart", "-k", "gui/<uid>/com.cec.vivisystem.listener"]` with no shell; audit plus status-channel notice. No background scheduler exists in the app.
- **Audit:** append-only JSONL with `at`, `actor`, `action`, `target`, `outcome`, `request_id`, `remote_addr`, short user agent, and old/new channel IDs for channel edits.

## 31a — read-only dashboard (two sessions)

| Deliverable | Content |
| --- | --- |
| App shell | Factory, routes S0–S5, S8 and read-only S6/S7; templates; CSS tokens; copy catalog EN/TC; language switch per account |
| Auth | Users file `data/control/users.json` (mode 0600), CLI `adduser`, sign-in/out, lockout, sessions, CSRF, password re-entry helper (used by 31b) |
| Status | `status_snapshot` readers; `launchctl print` parser with macOS guard ("Not available on this computer" elsewhere) |
| Calendar and dates | Reader with fake client in tests; explicit error and auth-expired states; important dates through `next_occurrence` |
| Audit | Sign-in success/failure and sign-out; monthly JSONL; 90-day purge on start and daily |
| Ops | LaunchAgent template; maintenance runbook for LAN bind, DHCP reservation, user creation and emergency local access; ADR 0015 |

## 31b — guarded actions (two sessions)

| Action | Flow |
| --- | --- |
| Slack channels | Edit `SLACK_FAMILY_PLANS_CHANNEL_ID`, `SLACK_LIFE_NOTES_CHANNEL_ID`, `CEC_HEALTH_ALERT_CHANNEL_ID` only. Validate format `^[CG][A-Z0-9]{8,}$`; optional test message (confirmed). Preview, then atomic line-preserving write and a backup in `data/control/env-backups/` (last 3, mode 0600). Audit plus status notice, then the prompt "Restart the listener to use the new channel". |
| Google access (admin and member) | **Start:** consent URL for the Desktop client with loopback `redirect_uri`, scope `calendar.events`, `access_type=offline`, `prompt=consent`, PKCE S256 and `state`; the attempt lives in memory for 10 min. **Finish:** paste the redirected address, check `state` and age, exchange the code through an injectable HTTP call, write `GOOGLE_REFRESH_TOKEN` and `GOOGLE_REFRESH_TOKEN_ISSUED_AT` (HKT ISO), probe, show the expiry date, prompt the restart. ADR 0016. |
| Test connection | `probe_google_client` on demand; result and time shown and audited (no token values) |
| Stuck-retry setting (admin) | Edit `CEC_LISTENER_STUCK_RETRIES` (0–10) on S2 with the same allowlist/backup flow as channels; restart prompt. |
| Restart listener | Confirmation screen with consequence copy, CSRF, password re-entry if stale, 5-min rate limit, fixed-argv kickstart. Poll until the heartbeat shows a new `instance_id` and connected, or show the error. Audit plus status notice. |

## Locked test plan (draft — lock per slice when decisions are confirmed)

Starlette `TestClient` (adds `httpx` to the dev group), fixed HKT clock, temporary data dirs, fake calendar client, fake poster, fake process runner and fake HTTP. No network, real launchd or real Tailscale.

**31a**

| ID | Case | Expect |
| --- | --- | --- |
| C1 | Overview from healthy fixtures (heartbeat ok, recap posted, token 6 days) | All cards show state words with shape glyphs in EN and TC; HTTP 200 |
| C2 | Heartbeat stale, missing or garbage | "No Slack connection" / "Stopped" / "No data"; no 500 |
| C3 | Token 2 days / 0 days / missing issued-at | Warn banner / error banner / "unknown" with guidance |
| C4 | Pending recap marker | "Delivery is not confirmed" with the ADR 0009 note; no run button anywhere |
| C5 | Calendar with fake events; list failure; auth error | Grouped by HKT day with exclusive end and all-day labels; explicit error text; re-authorize hint |
| C6 | Important dates, including a yearly 29 Feb | Ordered by `next_occurrence`; read-only |
| C7 | Unauthenticated GET; wrong password ×5; correct password; sign out | Redirect to sign-in; lockout at 5 with a fixed clock; cookie flags (`__Host-`, `Secure`, `HttpOnly`, `SameSite=Strict`); session cleared |
| C8 | POST without CSRF token; with token; wrong `Origin` | 403 / ok / 403 |
| C9 | Host header not on the allowlist | 400 |
| C10 | Sentinel secret values in env; crawl every GET route as admin | No sentinel in any body, header or captured log |
| C11 | Response headers | CSP, `nosniff`, `no-referrer`, `no-store`, `frame-ancestors 'none'` present |
| C12 | Audit on sign-in success/failure; purge at 91 days | JSONL fields present; old files removed |
| C13 | Copy catalogs | Key parity EN/TC; no empty strings; no Simplified markers (`parser.contains_simplified_markers`); EN instruction strings ≤ 20 words |
| C14 | Static guard | Control package references no `write_calendar_create`, `create_event`, `resolve_confirmation` or `kickstart` (31a) |
| C15 | `launchctl print` fixture; non-macOS | State/pid/last exit parsed; "Not available on this computer"; runner never called with `kickstart` |
| C16 | Import isolation | Importing `cec_vivisystem.listener` does not import `starlette` |
| C17 | Boundary logs | `control_request` with route, status and `duration_ms`; no query strings, cookies or tokens |

**31b**

| ID | Case | Expect |
| --- | --- | --- |
| A1 | Valid channel ID edit | Only that key changes; other `.env` lines byte-identical; backup 0600 (last 3 kept); audit with old/new IDs; status notice; restart prompt |
| A2 | Invalid format; secret key in the form | Error copy; `.env` unchanged; 400 for secret keys |
| A3 | `.env` write raises `OSError` | Error shown; original file intact; no backup rotation |
| A4 | Sensitive action 16 min after the last password entry | Password prompt before the action |
| A5 | OAuth start | URL has client ID, loopback redirect, `calendar.events`, `access_type=offline`, `prompt=consent`, `code_challenge_method=S256`, `state`; attempt stored with 10-min TTL |
| A6 | OAuth paste, success | Fake exchange returns a refresh token; `.env` token keys written with HKT `ISSUED_AT`; probe called; expiry date shown; token absent from response, log and audit |
| A7 | OAuth errors: wrong `state`, expired attempt, `error=access_denied`, no `code`, exchange 400, no refresh token | Specific STE error per case; `.env` unchanged |
| A8 | Restart, confirmed | Runner called once with the exact argv; audit; status notice; success copy shows the new PID from the fake heartbeat |
| A9 | Second restart within 5 min; runner fails; non-macOS | Refused with time copy / error copy / unavailable copy; runner not called on refusal |
| A10 | Health stale fixture with no user action | Runner never called; no background tasks registered |
| A11 | Member role: channel edit, restart and user management give 403; Google re-authorization and Test connection are allowed | per decision 3 |
| A12 | Test message to a channel | Fake poster called once with STE test text; failure copy on error |

Non-tests: real Slack, Google, LAN/TLS and launchd; Safari rendering and CSP enforcement in a real browser; iOS Home Screen behavior; TLS; visual regression snapshots; performance.

Target about 30 (31a) and 20 (31b) new cases, plus the full suite, Ruff and `git diff --check`.

## Logging and retention

- **Class A** (`component=control`, 14 days):
  - `control_request` (INFO; route, status, `duration_ms`)
  - `control_sign_in` (`outcome`, `reason` on failure), `control_lockout` (WARNING)
  - `control_action_started` / `control_action_finished` (`action`, `outcome`, `duration_ms`)
  - `control_env_write_failed` (ERROR), `control_oauth_failed` (ERROR, error class only)
- **Class B:** `data/control/audit/YYYY-MM.jsonl`, 90 days; purged on start and daily. Admin actions are also posted to the status channel.
- **Class C:** `data/control/env-backups/` (last 3, maximum 30 days; they contain secrets, so mode 0600, kept under the ignored `data/`). Pending OAuth attempts and sessions live in memory only.
- **Local credentials** (ground rule 13, not a retention class): `data/control/users.json` holds scrypt hashes, mode 0600. An admin removes entries with the CLI.
- **Class F and class G:** important dates are read-only. The calendar is read-only with a 60-s in-memory cache and no mirror.

## Operator rollout (authorized separately)

1. Give the Mini a DHCP reservation on the router. Do not port-forward. (HTTPS with mkcert is a later, optional step.)
2. After the reviewed merge: run `uv sync --locked --extra control`, then the installer (`--dry-run` first). Create the accounts with `adduser` (Carter `--role admin`, wife `--role member`). Set `CEC_CONTROL_BIND` and `CEC_CONTROL_ALLOWED_NETS`.
3. Smoke test from each phone on home Wi-Fi (and confirm that the app is not reachable on mobile data): sign in, Overview, Calendar, Dates; Add to Home Screen; sign out.
4. 31b: choose a quiet window, re-authorize Google once, restart the listener from the app, confirm the status notice and a Slack `help` reply.

## Implementation notes (2026-10-20)

Delivered in one offline PR (31a and 31b). Files: `src/cec_vivisystem/control/`
(`app.py`, `auth.py`, `audit.py`, `config.py`, `envfile.py`, `launchd.py`,
`oauth_google.py`, `snapshot.py`, `i18n.py`, `demo.py`, `__main__.py`,
`copy/en.json`, `copy/zh-Hant-HK.json`, `templates/`, `static/`),
`deploy/launchd/com.cec.vivisystem.control.plist.template`, the installer fix,
`calendar_reader.group_by_hkt_day` (shared with `format_recap`), tests
`test_control_app.py` (31a), `test_control_actions.py` (31b),
`test_control_units.py`, `control_support.py`, and new installer cases in
`test_launchd_templates.py`.

Differences from the draft above:

- **Status readers.** No shared `status_snapshot.py` yet: `control/snapshot.py`
  composes the checker's own readers (`health.check_heartbeat`,
  `health.read_alert_state`, `daily_status` samples, recap and dates-job states,
  `google_token_reminder` expiry). Job run records (Phase 30c backlog) do not
  exist, so the Jobs screen uses markers, dated logs and samples.
- **Referrer-Policy** is `same-origin`, not `no-referrer`: with `no-referrer`
  browsers send `Origin: null` on same-origin form posts (ADR 0015).
- **Routes.** Overview `/`; `/calendar`; `/dates`; System: `/system/listener`
  (+ `/restart`, `/restart/status`, `/stuck-retries`), `/system/jobs`,
  `/system/activity`; Settings: `/settings/channels` (+ `edit`, `preview`,
  `save`, `test`), `/settings/google` (+ `test`, `start`, `finish`),
  `/settings/account`, `/settings/users` (admin, read-only list).
- **Sensitive actions** (channel save, stuck-retry save, restart, Google start)
  ask for the password inside the same form when the last entry is older than
  15 minutes; Google finish needs no re-entry because its attempt is bound to
  the session and is at most 10 minutes old.
- **Bot channel membership** ("✓ bot") is not shown: it needs extra Slack scopes.
- **Test message** (A12) is implemented with a confirmation screen.
- **Demo mode** (`python -m cec_vivisystem.control demo`) serves synthetic data
  on loopback only, with `/demo/signin/<role>`; `serve` never registers it.
- **Member restart gap.** A member's re-authorization reaches Slack replies only
  after an admin restarts the listener (roles unchanged; open question).

Tests (97 new; 696 total): C1–C17 and A1–A12 as locked above, plus units
(config, scrypt accounts, limiter, sessions, `.env` writer and backups,
launchd parsing, PKCE RFC 7636 vector, CLI), the copy catalogs (parity,
placeholders, Simplified characters, STE banned words, ≤ 20 words per English
sentence), the static Calendar guard, import isolation, WCAG AA contrast of the
shipped CSS tokens in light and dark, and the installer's bootout wait,
bootstrap retry and non-zero exit (fake `launchctl` on PATH).

## Acceptance

- [x] Carter confirmed decisions 1–6 (network LAN-only, Testing OAuth and English default decided 2026-10-15; member role approved; HTTP on LAN first; details locked 2026-10-20)
- [x] 31a tests locked, implemented and green; ADR 0015; maintenance runbook; logging-and-retention rows; PROGRESS
- [x] 31b tests locked, implemented and green; ADR 0016; maintenance updated; PROGRESS
- [ ] STE wording checked against the current ASD-STE100 dictionary (or an STE checker); Traditional Chinese reviewed by a family member (style rules and banned words are tested; no certified STE check)
- [x] Contrast pairs in the design note §8.2 hold in the shipped CSS (automated test, light and dark)
- [ ] Mini (authorized): both phones reach the app on the home LAN only; no listener on `0.0.0.0`; not reachable from outside
- [ ] Mini (authorized): one re-authorization and one restart done from the app, each audited and announced

## Out of scope

- Calendar writes of any kind (31c needs its own design, including cross-process confirmation-store safety, because the intake lock is process-local).
- Scheduled-job reruns or reconciliation automation.
- Important-date edit/delete.
- Note search.
- Series or delete views (after PR #4).
- Push notifications.
- Native apps.
- Public exposure or port forwarding.
- An off-box checker.
