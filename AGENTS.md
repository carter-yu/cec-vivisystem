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
| `calendar_writer.py` | Accepted-create gate, audit store, Google client for create/list, stable provider IDs, pagination and stale-transport retry |
| `calendar_reader.py` / `overlap.py` | Read-only lists/period recaps and advisory overlap warnings |
| `life_notes.py` / `important_dates.py` | Separate durable raw-note and yearly/one-off date stores; important-date add does not require Calendar confirmation |
| `morning_recap.py` / `google_token_reminder.py` | Independent scheduled Slack commands; important-date command also runs the token reminder |
| `parse_misses.py` | Rule-miss records and keyword counts for later rule improvements |
| `storage.py` / `logging.py` | Atomic file replacement; structured console/file logs and log retention |

## Contracts to preserve

- Google Calendar is the source of truth for time-based events. Calendar writes
  require explicit human confirmation and must go through `write_calendar_create`.
  Update/delete are not implemented; do not bypass the gate to add them.
- `parse()` stays rules-only for offline compatibility. ADR 0008 makes listener
  creation LLM-first when configured, after deterministic control routing; do not
  expand the create vocabulary as the normal fix for new wording. No direct model
  writes or model-driven list/important-date/note processing. Model outages fail
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
  never add a competing forced endpoint reconnect loop.
- Overlap checks warn; they do not prohibit an explicitly confirmed create.
- Engineering prose is English. Runtime Chinese is Traditional, with Cantonese
  and English interaction. Use synthetic public examples and fixtures; do not
  copy private incident text into tests or documentation.

## Environment and commands

Python is pinned to 3.12; package metadata requires >=3.12. Use uv and the checked-in
`uv.lock`; Hatchling builds the package. No project-specific type-check command,
type-checker dependency, or tracked CI workflow is currently configured.

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
git diff --check
```

With an existing environment, these avoid dependency synchronization and repository
bytecode/test/lint caches:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m ruff check --no-cache .
```

Packaging uses `uv build` (not a live service command). See README for runtime
invocations of module `main()` functions. Root `main.py` only runs the hello demo.
The 07:00 recap and 10:00 important-date/token jobs require external scheduling;
starting the listener does not install or run these schedules.

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
- Logging uses `LOG_LEVEL`, `CEC_LOG_DIR`, and `CEC_LOG_TO_FILE`. Log filenames
  use the host's local date; scheduling assumes the host is configured for HKT.

## Safety and verification

- Do not read or expose `.env`, live `data/`, incident logs, or private operator
  notes when code/contracts suffice. Never commit credentials or family records.
- Live listener/recap/date/token commands contact Slack or Google; they are not
  smoke tests to run during an ordinary code review. Do not send messages or
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
  Calendar update/delete, freebusy, Reminder Agent, Observer, note search, and
  important-date edit/delete remain future scope.
