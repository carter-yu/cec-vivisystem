# Repository onboarding review — 2026-09-22

## Verified facts and scope

Reviewed the existing project at `main`, commit `439f46f` (Phase 21 takeover
reliability fixes). The only pre-existing working-tree change on resumption was
the earlier onboarding pause entry in `PROGRESS.md`; it is preserved.

The review used root instructions, README, architecture/ground rules, maintenance,
testing/resilience/retention standards, ADRs 0001–0006, prior takeover findings,
selected phase specifications, package configuration, source implementation paths,
and tests. It did not read secrets, live stores, or incident-log bodies. This is
a repository onboarding and contract review, not an exhaustive security audit or
proof of every natural-language input.

Fresh verification with the existing local environment:

| Check | Result |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider` | 281 passed |
| `.venv/bin/python -m ruff check --no-cache .` | All checks passed |

Tests use fake provider boundaries, fixed clocks, and temporary/in-memory stores.
These results reproduce the prior documented test count but do not verify live
Slack, Google, model calls, or deployment. Dependency installation, packaging,
remote synchronization, and service restarts were not performed.

## Purpose and architecture

The project supports family scheduling in Cantonese/English, explicit confirmation
before Calendar creation, read-only event lists, raw life-note capture, important
dates, and scheduled Slack summaries/reminders. Runtime Chinese is Traditional.

```text
Slack Socket Mode -> normalize/filter -> synchronous listener dispatch
  plans -> rules -> optional create-only LLM fallback -> proposal + overlap warning
        -> pending confirmation -> thread yes -> Calendar Writer -> Google Calendar
  list requests -> Calendar Reader -> Slack reply
  important-date add/list -> separate JSON catalog -> Slack reply
  notes channel -> exact raw-text JSON store -> Slack acknowledgement

External scheduler -> morning_recap.main -> today's Calendar list -> Slack
External scheduler -> important_dates.main -> next-7-days dates + token reminder -> Slack
```

`models.py` supplies dataclass/enum contracts. Protocols and injected fakes separate
business logic from provider and persistence boundaries. There is no broker,
database service, web UI, LangGraph, or central model agent in the implementation.
The interactive listener is a shared dependency; scheduled commands run separately.

Key verified behaviors:

- `parser.parse` is rules-only; `parse_with_fallback` rejects non-create routes
  and validates model output before it can become a proposal.
- `confirmation.create_confirmation` derives Slack-origin IDs from channel/source
  message, preserves existing terminal records, and defaults to 24-hour expiry.
  Resolution checks expiry directly; listener intake also expires due records.
- `write_calendar_create` requires an accepted create with an ID and valid start/
  end. Google payload IDs derive from the confirmation. Conflict recovery checks
  confirmation ownership and cancellation status. Audit success also short-circuits
  repeat writes. `tests/test_review_regressions.py` exercises lost responses,
  pagination, duplicate delivery, concurrent intake, expiry, and atomic-write failure.
- Google listing follows all pages. Overlap checks are advisory. Period recaps
  clip spanning events to the requested dates.
- `storage.atomic_write_text` flushes/fsyncs a temporary sibling before replacement.
  All seven JSON store modules use this helper. This is not a multi-file transaction.
- The listener serializes intake with an `RLock`; slow external requests hold it.
  Scheduled processes do not share this lock.

## Stack, layout, commands, and configuration

Python 3.12 is pinned, with >=3.12 required in `pyproject.toml`. uv manages the
checked-in lockfile; Hatchling is the build backend. Direct runtime dependencies
are Google API client/auth, OpenAI SDK, python-dotenv, Slack Bolt, and structlog.
pytest and Ruff are the development dependencies. No type checker or tracked CI
workflow is configured. Type annotations alone are not a type-check gate.

| Path | Role |
| --- | --- |
| `src/cec_vivisystem/` | Runtime modules; detailed module map in root AGENTS.md |
| `src/cec_vivisystem/prompts/` | Versioned LLM prompts; live parser selects v2 |
| `tests/` | Component suites and Phase 21 regressions |
| `phases/` | Incremental scope and locked regression plans, through Phase 21 |
| `docs/decisions/` | Six ADRs |
| `data/`, `logs/` | Runtime state/log roots; contents ignored except `.gitkeep` |
| `main.py` | Hello demo, not the family service |

Setup: `uv sync --locked`. Tests: `uv run pytest -q`. Lint: `uv run ruff check .`.
Packaging: `uv build`, derived from the uv/Hatchling setup; not exercised here.
No project-specific type-check command exists. Ruff formatting is a documented
tool choice, but no formatter gate or custom Ruff configuration is present.

README invokes runtime entry points as
`uv run python -c "from cec_vivisystem.listener import main; main()"`, with the
module replaced by `morning_recap`, `important_dates`, or `google_token_reminder`
for the scheduled commands. `important_dates.main` includes the token reminder.
`parse_misses.main` summarizes local misses and also runs retention. `hello.main`
and `parser.main` are demos; default logging can write/purge local logs.

The live listener requires both channel IDs plus Slack bot/app tokens. Calendar
configuration requires client ID, client secret, and refresh token; target calendar
defaults to `primary`. Missing Calendar configuration allows listener startup in
a degraded mode. Scheduled posting requires bot token and plans channel; morning
recap additionally needs Calendar credentials. `.env.example` lists placeholders.

Optional fallback accepts either `XAI_API_KEY` or `LLM_API_KEY`, with configurable
base URL/model/fallback model. Its configured provider endpoint and model strings
were inspected as code, not verified against a live provider catalog. Token
reminders calculate dates from configured issuance time plus TTL (default seven
days); they do not query actual token validity. Scheduling is external launchd
configuration, with no tracked plist or deployment automation found.

## Documentation contradictions and qualifications

| Documentation claim or implication | Verified implementation / interpretation |
| --- | --- |
| Ground rules say a future ADR may allow LLMs and “Until then: no LLM” | ADR 0005 already grants a narrow fallback exception, implemented in `parse_fallback.py`. Rules-only parsing remains the default contract. The conditional prose needs updating, not removal of fallback. |
| Early architecture prose emphasizes event/message coordination | Current code uses direct synchronous calls. Architecture's later current-state section correctly acknowledges this; read that qualification with the diagram. |
| `.env.example` offers `GOOGLE_CREDENTIALS_PATH` | The Calendar config loader only consumes the three OAuth values and calendar ID. The credential-file path is not implemented. `SLACK_SIGNING_SECRET` is also not consumed by the current Socket Mode path. |
| Maintenance broadly says mapped entry points load `.env` and may contact services | `parse_misses.main` does neither; it accesses local data, enables normal logging, and purges expired misses before summarizing. Do not treat it as a read-only inspection command. |
| README/Phase 20 imply timeout changes eliminate long hangs | Code configures 15 seconds per model with zero SDK retries and up to two model attempts. There is no whole-interaction deadline, and intake waits behind the listener lock. Tests do not prove a live latency bound. |
| “Always reply” and “one post” wording | Listener produces reply text for list failures/empty results, but live `say` can fail. Scheduled markers suppress repeats only after successful persistence; Slack post plus marker save is not atomic. |
| Historical prose says aliases occur only in parser tables and public examples are synthetic | Prompt files also carry aliases, and historical docs/demo examples retain them. The latest fixture policy should guide new artifacts; blanket claims about all existing content are too broad. |
| Retention table describes 90-day confirmation-decision audit | `resolve_confirmation` saves class-C state and emits application logs; the separate class-B store records Calendar write outcomes. Rejected/expired decisions do not have a separate 90-day audit row. Clarify whether the broader policy is still required. |

The existing Phase 21 handover is substantially consistent with implementation.
Old phase exclusions are historical scope, not evidence that later features are
missing. No production fixes or broad historical-document rewrite were made here.

## Current risks and unfinished areas

1. **Scheduled delivery:** all three scheduled flows post before recording local
   markers. Marker-save failures are logged while the result can remain POSTED.
   Reruns can duplicate messages. `SlackWebPoster.post` discards the provider
   response, so reconciliation lacks a stored message handle.
2. **Durable data:** several JSON readers log and skip corruption or return None,
   potentially presenting partial/absent content. Atomic replacement protects
   individual updates but supplies neither backups nor tested restore.
3. **Maintenance:** listener startup purges confirmation/audit/miss stores. Intake
   expires confirmations but does not continuously purge them. Log maintenance
   also runs on the first emitted log of a new host-local date; caps are not
   continuously enforced. `setup_logging` startup filesystem errors can still
   raise, despite runtime file-write errors falling back to console.
4. **Concurrency and authority:** one process per store is the supported model.
   Confirmation lookup uses channel/thread/status, not proposal-owner identity.
   Any accepted human message in the allowed thread can resolve the latest pending
   proposal; multiple proposals per thread are ambiguous. This assumes trusted
   family channel membership and is not a per-user authorization system.
5. **Retry history:** stable IDs cover new writes, but historical Google-generated
   events still require their prior audits. No historical duplicate cleanup exists.
6. **Parsing/model limits:** ordered title matching can discard qualifiers or choose
   one activity from compound text. Script rejection is a marker set, not complete
   classification. Fake-model tests validate contracts, not novel-phrase quality.
7. **Future scope:** Calendar update/delete, freebusy, full Reminder Agent, independent
   Observer, note search/enrichment, important-date edit/delete, backup/restore,
   and independent maintenance/health tooling are not completed features.

## Assumptions and unresolved questions

- Mini deployment, schedules, timezone, running revision, credential validity, and
  actual backups remain unverified. The repository records rollout as pending;
  that is not direct evidence of the current machine state.
- Is every family-channel member intended to be able to confirm another member's
  proposal? Current code permits it. Decide before expanding access or editing.
- Should confirmation decisions be retained independently for 90 days, as the
  general retention table suggests, or is current operational retention sufficient?
- What delivery policy should ambiguous scheduled posts follow: reconcile, retry
  with possible duplication, or require operator intervention?
- Default data/log paths derive from the source file's location. Checkout-based
  operation is documented; an installed-wheel deployment was not validated.
- Private setup notes and an external sibling LLM constitution are referenced by
  documentation. Their contents and current operational applicability were not
  inspected; the tracked repository is not a complete deployment/recovery runbook.

## Recommended next actions

1. Verify the existing Mini rollout and preserve its private stores/audits before
   any deployment. Use an explicitly authorized synthetic live smoke; do not infer
   live readiness from the green offline suite.
2. Lock a small phase for scheduled-post reconciliation with injected failures
   between post and marker save. Define delivery policy before implementing it.
3. Add tested backup/restore and visible corruption reporting for durable content,
   then independent operational maintenance and last-success health checks.
4. Reconcile the configuration, latency, command side-effect, and confirmation
   audit documentation above. Preserve historical context and avoid a rewrite.
5. Expand parser/prompt behavior only from demonstrated needs with synthetic
   regression cases; keep the confirmation gate and rules-first boundary.
