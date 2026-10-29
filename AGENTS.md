# cec-vivisystem maintenance context

This is an existing family scheduling and raw-notes system. Continue from its
implemented components; do not re-scaffold it. Prefer focused fixes and existing
contracts over speculative architecture changes.

## Start a session

1. Read `docs/ground-rules.md`, `docs/architecture.md`, and the latest `PROGRESS.md`
   entry before changes.
2. Read `docs/maintenance.md` for operations, `docs/onboarding-review-2026-09-22.md`
   for verified onboarding findings, and `docs/takeover-review-2026-09-19.md` for
   Phase 21 fixes and remaining backlog.
3. Check Git status and preserve existing working-tree changes.
4. Read the relevant source, tests, phase specification, and ADR before changing
   behavior. Historical phase exclusions describe their time, not current scope.

## Implementation map

Code lives in `src/cec_vivisystem/`; shared contracts are dataclasses/enums in
`models.py`. Runtime dispatch is synchronous Python function calls with injected
clients/stores, not a message broker or a distributed agent framework.

| Module | Responsibility |
| --- | --- |
| `listener.py` | Slack Socket Mode; normalize/filter messages; dispatch plans, thread confirmations, raw notes, and important dates |
| `parser.py` | Offline rules returning `ParseResult`; help, Calendar create/list, important-date add/list, clarification, unknown |
| `parse_fallback.py` / `prompts/` | LLM-first create extraction when configured; legacy fallback/offline compatibility; same confirmation path |
| `confirmation.py` | Proposals, 24-hour pending expiry, thread resolution, source-derived confirmation IDs |
| `calendar_writer.py` | Accepted-confirmation gate, audit store, Google client for create/list/delete, stable provider IDs (per occurrence for series), pagination and stale-transport retry |
| `series.py` | Phase 27 span guard, weekday-series cue/text extraction and pure capped expander (ADR 0011) |
| `event_batch.py` | Phase 33a list create (ADR 0018): per-item checks, not-included reasons, dated-list routing guard, proposal copy, `唔要 N` / `只要 1,4` narrowing |
| `image_intake.py` / `image_extract.py` | Phase 33b images (ADR 0019; `CEC_IMAGE_EXTRACT` default `off`): `加event` trigger, PNG/JPEG filters, guarded `files.slack.com` download, Pillow metadata strip/resize, xAI vision → 33a `events[]` and the same confirmation path |
| `calendar_delete.py` | Phase 28 read-only delete-one target matching over one HKT day (ADR 0012) |
| `calendar_reader.py` / `overlap.py` | Read-only lists/period recaps and advisory overlap warnings |
| `life_notes.py` / `important_dates.py` | Separate durable raw-note and yearly/one-off date stores; important-date add does not require Calendar confirmation |
| `morning_recap.py` / `google_token_reminder.py` | Independent scheduled Slack commands; important-date command also runs the token reminder |
| `health.py` | Phase 29/30 listener heartbeat (v2) and alert-only checker (`python -m cec_vivisystem.health`); never restarts anything |
| `health_policy.py` / `status_messages.py` / `daily_status.py` | Phase 30 pure alert policy v2, EN + TC status-channel catalog, checker samples and 09:00 daily status |
| `status_channel.py` | System-status channel (`CEC_HEALTH_ALERT_CHANNEL_ID`) with logged plans-channel fallback |
| `stuck_connection.py` | Phase 30 pure stuck-connection guard; the listener's only self-exit (code 75, ADR 0014) |
| `parse_misses.py` | Rule-miss records and keyword counts for later rule improvements |
| `medic_librarian/` / `redaction.py` | Phase 32 `#jeff-aia-medic` Q&A (ADR 0017): offline `build` (pdftotext → redacted knowledge tree), keyword retrieval, model answer limited to retrieved nodes with code-rendered page sources; shared `redact_sensitive`. Never Calendar |
| `control/` | Phase 31 LAN web app (optional `control` extra; ADRs 0015/0016): `app.py` routes and guards, `auth.py` scrypt accounts/sessions/lockout, `envfile.py` allowlisted `.env` writes + backups, `launchd.py` read + listener kickstart, `oauth_google.py` PKCE re-authorization, `snapshot.py` status readers, `copy/*.json` EN + TC catalog, `demo.py` loopback-only synthetic demo |
| `storage.py` / `logging.py` | Atomic file replacement; structured console/file logs and log retention |

## Contracts to preserve

- Google Calendar is the source of truth for time-based events. Calendar writes
  require explicit human confirmation and must go through `write_calendar_create`,
  `write_calendar_series_create` (bounded weekday series, cap 40),
  `write_calendar_batch_create` (Phase 33a list of up to 15 items, ADR 0018) or
  `write_calendar_delete` (one listed target). Update/patch and series-delete are
  not implemented; do not bypass the gate to add them. A single timed create
  crossing HKT days never becomes a proposal or a write.
- `parse()` stays rules-only for offline compatibility. ADR 0008 makes listener
  creation LLM-first when configured, after deterministic control routing; do not
  expand the create vocabulary as the normal fix for new wording. No direct model
  writes or model-driven list/delete/important-date/note processing. Model outages fail
  visibly; no credentials selects offline rules. Tests use fake models. Never
  infer a whole-interaction deadline from the 15-second per-model timeout.
- Preserve `Asia/Hong_Kong` time semantics, exclusive end boundaries, and the
  current one-hour default for timed creates. Inject clocks in tests.
- Preserve exact life-note `raw_text`. Life notes and important dates are separate
  class-F content, retained until family deletion; never purge them as logs.
- Keep Slack source-derived IDs and Calendar event-ID derivation stable. Reuse
  terminal confirmations on redelivery. Retry uncertain writes in the same thread;
  a new source message is a separate request. Historical Google-generated IDs
  still depend on their existing success audits.
- Operate one listener per data directory and avoid overlapping scheduled runs.
  The listener lock is process-local and covers external calls. Atomic JSON
  replacement is neither a cross-process transaction nor a backup system.
- Slack SDK owns Socket Mode recovery (Phase 22). Health checks are observational;
  never add a competing forced endpoint reconnect loop. The only exception is the
  bounded ADR 0014 stuck guard (whole-client replacement, then exit 75).
- Operator/system messages go to the system-status channel; family posts stay in
  `#family-plans`. Status posts are English (STE style) then Traditional Chinese.
- The control app (Phase 31) is LAN-only: never bind `0.0.0.0` or a public
  address, keep the client-IP and Host allowlists, CSRF + Origin on every POST,
  and server-side roles (member: view, Google re-authorization, Test
  connection; admin: also channels, stuck retries, restart, users). A
  successful Google re-authorization (either role) restarts the listener
  automatically through the same guarded helper, without the 5-minute gap
  (ADR 0016 amendment); no other automatic restart exists. Secrets
  reach templates as "Set"/"Not set" only. It never writes Calendar, never
  resolves confirmations, never reruns jobs and has no background tasks; the
  listener and jobs must never import it or Starlette.
- The medic channel (`CEC_MEDIC_CHANNEL_ID`, ADR 0017) is routed before any
  Calendar path: no parse, confirmation, Calendar, important dates or notes there.
  Redact with `redact_sensitive` before any model call or tree write; answers
  cite only retrieved tree nodes. Never track PDFs or `agent_librarian/`.
- Images (Phase 33b, ADR 0019) are read only in `#family-plans`, only from a
  top-level message whose text contains `加event`, and only with
  `CEC_IMAGE_EXTRACT=xai`; every other message behaves as with the switch off.
  The bot token goes only to `https://files.slack.com` (redirects elsewhere are
  refused before sending). Image items use the 33a confirmation path; nothing
  is stored beyond the confirmation.
- Overlap checks warn; they do not prohibit an explicitly confirmed create.
- Engineering prose is English. Runtime Chinese is Traditional, with Cantonese
  and English interaction. Use synthetic public examples and fixtures; do not
  copy private incident text into tests or documentation.

## Environment and commands

Python is pinned to 3.12; package metadata requires >=3.12. Use uv and the checked-in
`uv.lock`; Hatchling builds the package. No project-specific type-check command,
type-checker dependency, or tracked CI workflow is currently configured.

```sh
uv sync --locked --extra control
uv run pytest -q
uv run ruff check .
git diff --check
```

Without `--extra control` the control-app tests skip with a reason (the web
packages are an optional extra).

With an existing environment, these avoid dependency synchronization and repository
bytecode/test/lint caches:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m ruff check --no-cache .
```

Packaging uses `uv build` (not a live service command). See README for runtime
invocations of module `main()` functions. Root `main.py` only runs the hello demo.
The 07:00 recap and 10:00 important-date/token jobs require external scheduling;
starting the listener does not install or run these schedules. Tracked LaunchAgent
templates are in `deploy/launchd/`; `scripts/install_launchagents.sh` installs them
on the Mini (macOS only; use `--dry-run` elsewhere). FileVault stays on: agents run
only after a manual login (ADR 0013).

Use `.env.example` for variable names, never real values in tracked files:

- Listener requires `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`,
  `SLACK_FAMILY_PLANS_CHANNEL_ID`, and `SLACK_LIFE_NOTES_CHANNEL_ID`.
- Calendar requires `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and
  `GOOGLE_REFRESH_TOKEN`; `GOOGLE_CALENDAR_ID` defaults to `primary`.
  The current client does not consume `GOOGLE_CREDENTIALS_PATH`.
- LLM fallback accepts `XAI_API_KEY` or `LLM_API_KEY`; model/base URL/fallback
  overrides are loaded in `parse_fallback.load_live_llm_parsers`.
- Token reminders use configured `GOOGLE_REFRESH_TOKEN_ISSUED_AT` and optional
  `GOOGLE_TOKEN_TTL_DAYS` (default 7), not a live expiry query.
- Control app (Phase 31): `CEC_CONTROL_BIND` (default `127.0.0.1:8730`; LAN or
  loopback IP only), `CEC_CONTROL_ALLOWED_NETS`, `CEC_CONTROL_HOSTS`, optional
  `CEC_CONTROL_TLS_CERT` + `CEC_CONTROL_TLS_KEY`. Accounts:
  `python -m cec_vivisystem.control adduser <name> --role admin|member`.
- Medic librarian (Phase 32): `CEC_MEDIC_CHANNEL_ID` (default `C0CB179V0AE`;
  `off` disables), optional `CEC_MEDIC_KB_DIR`; answers reuse the LLM keys above.
- Image extract (Phase 33b): `CEC_IMAGE_EXTRACT` (`off` default, `xai` enables),
  optional `LLM_VISION_MODEL` / `LLM_VISION_FALLBACK_MODEL`; reuses the LLM keys
  above; needs the Slack bot scope `files:read`.
- `CEC_HEALTH_ALERT_CHANNEL_ID` is the system-status channel. Listener stuck
  guard: `CEC_LISTENER_STUCK_AFTER_S` (900), `CEC_LISTENER_STUCK_RETRIES` (3,
  0–10), `CEC_LISTENER_STUCK_RETRY_WAIT_S` (120).
- Logging uses `LOG_LEVEL`, `CEC_LOG_DIR`, and `CEC_LOG_TO_FILE`. Log filenames
  use the host's local date; scheduling assumes the host is configured for HKT.

## Safety and verification

- Do not read or expose `.env`, live `data/`, incident logs, or private operator
  notes when code/contracts suffice. Never commit credentials or family records.
- Live listener/recap/date/token commands and `control serve` contact Slack,
  Google or launchd; they are not smoke tests to run during an ordinary code
  review. `control demo` is the local, loopback-only, synthetic alternative. Do not send messages or
  deploy without task authorization. A code-review request does not authorize it.
- Offline does not mean read-only: `parse_misses.main()` purges expired records
  before printing counts. Demo CLIs can create logs and run log retention.
- Lock a phase regression plan before materially extending a component. Preserve
  fake clients, fixed clocks, temporary stores, and failure/contract coverage.
- Run pytest and Ruff, review the diff, and update PROGRESS at session end unless
  the user explicitly requested no edits. Record non-obvious decisions in an ADR.
- Distinguish local checks from deployment evidence. Do not claim Mini health,
  credential validity, remote synchronization, or model availability from tests.
- Known priorities: scheduled-post reconciliation, backup/restore and corruption
  reporting, periodic operational retention, and independent health checks.
  Calendar update/patch, series-delete, RRULE, freebusy, Reminder Agent, Observer,
  note search, and important-date edit/delete remain future scope.
