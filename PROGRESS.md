# Progress Log – cec-vivisystem

## How to use
Add a new entry at the top after every session (below this section, above older entries).

---

## 2026-08-28 (Phase 5B implementation)

- **Phase**: 5B – Slack wiring for LifeNotesKeeper **implemented**
- **Completed**:
  - Locked [phases/phase-5b-life-notes-slack.md](phases/phase-5b-life-notes-slack.md) (S1–S6, two named env vars, no mixed paths)
  - Listener dispatch: `#family-life-notes` → `create_life_note` (exact `raw_text`, `status=raw`, source metadata); `#family-plans` unchanged parse-reply
  - Config: `SLACK_FAMILY_PLANS_CHANNEL_ID` + required `SLACK_LIFE_NOTES_CHANNEL_ID`; `allowed_channel_ids` remains plans-only
  - Thin ack `已記低` on successful store; empty life-notes text ignored (`empty_text`); no empty notes
  - Tests S1–S5 in `tests/test_listener.py`; Socket Mode passes life-notes channel + `JsonDirLifeNotesStore`
- **Tests**: 61 passed; ruff clean
- **Issues / Friction**: Live smoke of `#family-life-notes` left to family after restarting Listener (previous live message was `wrong_channel` before this dispatch)
- **Resilience notes**: Offline fakes only in pytest; class F store unchanged (no auto-purge); secrets still local `.env`
- **Next session plan**: Slack confirmation (4b) or Calendar Writer — pick from need. Structured enrichment of stored notes is later.
- **Session status**: Phase 5B offline acceptance met

---

## 2026-08-28 (multi-channel Slack env names)

- **Phase**: 5A stretch prep — named channel env vars (no Listener dispatch yet)
- **Completed**:
  - Renamed `SLACK_ALLOWED_CHANNEL_ID` → `SLACK_FAMILY_PLANS_CHANNEL_ID` (maps to `#family-plans`)
  - Added placeholder `SLACK_LIFE_NOTES_CHANNEL_ID` in `.env.example` (maps to `#family-life-notes`; not read by code yet)
  - Gitignored `my-notes/` for local operator docs
  - Live smoke: Socket Mode still starts; a `#family-life-notes` message is logged `wrong_channel` (expected until wiring)
- **Tests**: 56 passed; ruff clean; hello/parser CLI smokes green
- **Issues / Friction**: `#family-life-notes` exists in Slack but Listener still allowlists only the plans channel
- **Resilience notes**: Secrets still local `.env` only; channel ids not committed
- **Next session plan**: Wire Listener dispatch for `#family-life-notes` (Phase 5A stretch / 5B) so messages call `create_life_note`
- **Session status**: Env rename committed; life-notes Slack wiring still open

---

## 2026-08-25 (Phase 5A implementation)

- **Phase**: 5A – LifeNotesKeeper Option A (reliable raw capture) **implemented**
- **Completed**:
  - `src/cec_vivisystem/life_notes.py` — `create_life_note`, `LifeNotesStore` protocol, `InMemoryLifeNotesStore`, `JsonDirLifeNotesStore` (`data/life_notes/`, gitignored)
  - Models: `LifeNote`, `LifeNoteStatus`, `LifeNoteSource`
  - Dedicated channel concept `#family-life-notes` (Listener wiring left as stretch)
  - `tests/test_life_notes.py` — LN1–LN5 plus contract, JSON store, incomplete source, logging boundary
  - Class **F** retention: notes kept until family deletes; no auto-purge; no LLM / no structured extraction / no calendar coupling
- **Tests**: 56 passed; ruff clean
- **Issues / Friction**: Slack `#family-life-notes` not wired yet (stretch)
- **Resilience notes**: Parallel component; injectable `now` + store; empty text / missing source raise `LifeNoteError`; store load failures logged and skipped; `raw_text` is the durable source of truth
- **Next session plan**: Wire Listener for `#family-life-notes` **or** Slack confirmation (4b) **or** Calendar Writer — pick from need. Structured enrichment of stored notes is later.
- **Session status**: Phase 5A offline acceptance met

---

## 2026-08-22 (Phase 4 implementation)

- **Phase**: 4 – Confirmation path **implemented** (offline core)
- **Completed**:
  - `src/cec_vivisystem/confirmation.py` — `build_proposal`, `create_confirmation`, `resolve_confirmation`, `expire_due_confirmations`, `purge_confirmations`, `maintain_confirmation_storage`
  - Models: `Confirmation`, `ConfirmationStatus`, `ConfirmationDecision`
  - Stores: `InMemoryConfirmationStore` + `JsonDirConfirmationStore` (`data/confirmations/`, gitignored)
  - `tests/test_confirmation.py` — C1–C10 locked cases
  - Class C retention: terminal+7d purge; pending max 30d; no calendar / no LLM / no Slack yes-no wire (stretch left open)
- **Tests**: 46 passed; ruff clean
- **Issues / Friction**: Listener still posts parse summary only — confirmation not yet created from Slack (Phase 4b stretch)
- **Resilience notes**: Write gate closed until Calendar Writer; purge path tested
- **Next session plan**: Wire Slack proposal + yes/no **or** start Calendar Writer for accepted confirmations only — decide from need. Freebusy still optional later.
- **Session status**: Phase 4 offline acceptance met

---

## 2026-08-22 (Phase 4 scope)

- **Phase**: 4 – Confirmation path **scope only** (not implemented)
- **Completed**:
  - Confirmed tip `eae3e0b` / 36 tests green; decided Phase 4 = **Option A – thin Confirmation** (not freebusy, not Calendar Writer, not LLM)
  - Wrote [phases/phase-4-confirmation.md](phases/phase-4-confirmation.md): goal, in/out, models, create/resolve/expire/purge, unit test plan C1–C10 (~10 tests), class **C** retention + purge mandatory, Slack yes/no as stretch
  - README + architecture: Confirmation / minimal Proposal → Scoped
- **Tests**: unchanged (36 passed; no confirmation code yet)
- **Issues / Friction**: Freebusy explicitly deferred to a later phase
- **Resilience notes**: First durable operational store must ship with purge; write gate still closed
- **Next session plan**: TDD implement Phase 4 from locked C1–C10. Do not start Calendar or freebusy until Confirmation acceptance is met.
- **Session status**: Phase 4 scope locked; ready for implementation

---

## 2026-08-22 (Phase 3 implementation)

- **Phase**: 3 – Rule parser expansion **implemented**
- **Completed**:
  - Heuristics: 聽日 (= tomorrow), 上晝/下晝 clock periods, Miss Wong 堂 title, 銅鑼灣 location; participant match adjacent to CJK (`帶Cedric去`)
  - Tests L1–L4 + L6 in `tests/test_parser.py`; Phase 1 suite still green
  - Module docstring documents relative/period policy; acceptance checked in phase doc
  - Architecture Parser → Done (Phase 1+3); README status updated
- **Tests**: 36 passed; ruff clean
- **Issues / Friction**: Optional live Slack re-smoke of L1 left to family; more dialect gaps may appear later (rules-first, not LLM by default)
- **Resilience notes**: Contract unchanged; no new stores; write gate still closed
- **Next session plan**: Decide Phase 4 from need (likely Confirmation path). Do not start Calendar CUD until Confirmation exists.
- **Session status**: Phase 3 implementation complete

---

## 2026-08-22 (Phase 3 scope)

- **Phase**: 3 – Rule parser expansion **scope only** (not implemented)
- **Completed**:
  - Confirmed git: `main` clean @ `12a65ad` (Phase 2 weekend close); fixed flaky file-log date assert (wall-clock vs injected retention `now`)
  - Decided Phase 3 = **Option A – parser rules expansion** from live friction (not Confirmation, not LLM)
  - Wrote [phases/phase-3-parser-expansion.md](phases/phase-3-parser-expansion.md): goal, in/out, fixtures L1–L4 (聽日 / 上晝 / 下晝 / Miss Wong 堂 + 銅鑼灣), unit test plan, class A logging only, acceptance criteria
  - README + architecture: Phase 3 scoped; next = implement expansion; Confirmation/Calendar parked
- **Tests**: 31 passed expected after log-test fix
- **Issues / Friction**: Live Cantonese still often `needs_clarification` until Phase 3 is implemented
- **Resilience notes**: No new stores; contract unchanged; LLM still demand-driven + ADR only
- **Next session plan**: TDD implement Phase 3 from locked L1–L4 (red → green heuristics). Do not start Confirmation or Calendar Writer until Phase 3 acceptance is met (or family explicitly re-prioritizes).
- **Session status**: Phase 3 scope locked; ready for implementation

---

## 2026-08-08 (weekend close)

- **Phase**: 0 done · 1 done · 2 done (live smoke on workspace **Three of Us** / `#family-plans`)
- **Saved**: `main` clean after this close; pushed to `origin/main` (carter-yu/cec-vivisystem)
- **Tests last known**: 31 passed
- **Live use**: Slack Socket Mode Listener works end-to-end (message → parse → thread reply; logs on stdout + `logs/{component}-YYYY-MM-DD.log`)
- **Known friction (grow from need)**: Real family Cantonese phrases often return `needs_clarification` (rule parser gaps: e.g. 聽日 / 上晝 / place+class titles). **Not** jumping to LLM by default — next session should decide Phase 3 scope from this friction *or* Confirmation path; rules-first enhance parser behind the same `ParseResult` contract unless sustained pain + ADR
- **Do not start next without scope**: Confirmation Guardian, Calendar Writer/read, LLM parser
- **Also this weekend**: secrets ground rule 13; file logs + startup archive/purge (class A, 14d / 100 MB); `logs/` gitignored
- **Next session plan**: Read PROGRESS + architecture §4.4–4.4.1; decide Phase 3 scope only first (candidates: **parser rules expansion from live phrases** *or* **Confirmation path** — pick what unblocks family use). Do not implement until `phases/phase-3-*.md` is locked
- **Session status**: Closed for this weekend

---

## 2026-08-08 (file logs + retention)

- **Phase**: ops / logging (post Phase 2 smoke)
- **Completed**:
  - File logging under `logs/{component}-YYYY-MM-DD.log` + `logs/archive/`
  - `setup_logging` runs archive (prior days) + purge (14d) + soft cap (100 MB) on every real service start
  - Docs §6 updated; `logs/` gitignored; unit tests for retention + per-component files
  - Pytest keeps stdout-only (no clutter in repo logs/)
- **Tests**: 31 passed
- **Next session plan**: live smoke with file logs (done same weekend)
- **Session status**: class A file sink + startup purge path live

---

## 2026-08-08 (Phase 2 implementation)

- **Phase**: 2 – Slack Listener **implemented**
- **Completed**:
  - `src/cec_vivisystem/listener.py` — `normalize_slack_event`, `should_accept`, `handle_inbound`, `process_slack_message_event`, `format_reply`, `load_slack_config`, Socket Mode `main`/`run_socket_mode`
  - Models: `InboundMessage`, `ListenerOutcome`, `ListenerResult` in `models.py`
  - Dependencies: `slack-bolt`, `python-dotenv` (live path only; unit tests stay offline)
  - `tests/test_listener.py` — L1–L7 + normalize + reply/handle paths
  - Reply text never claims calendar write; ground rule 13 honored (env-only secrets)
  - Phase doc acceptance checked; architecture Listener → Done; README run note for Socket Mode
- **Tests**: 26 passed (15 prior + 11 listener); ruff clean
- **Issues / Friction**: none material
- **Resilience notes**: No durable store; class A logs only; write gate closed; live smoke is manual with local `.env`
- **Next session plan**: Decide Phase 3 from need (likely Confirmation before Calendar Writer). Do not start Calendar CUD.
- **Session status**: Phase 2 implementation complete (manual Slack smoke left to family when tokens exist)

---

## 2026-08-08 (Phase 2 scope)

- **Phase**: 2 – Slack Listener **scope only** (not implemented)
- **Completed**:
  - Confirmed git: `main` clean @ `800cc47` (Phase 1 weekend close); 15 tests green
  - Decided Phase 2 = **Option A – Slack Listener** (thin intake: allowlisted channel → existing `parse` → reply summary; no calendar / confirmation / LLM)
  - Wrote [phases/phase-2-listener.md](phases/phase-2-listener.md): goal, in/out, Socket Mode preference, unit test plan (L1–L7, ~8–9 tests), logging/retention class A only, acceptance criteria
  - Ground rule **13 – Secrets Stay Local**: credentials only in never-committed `.env`; names/placeholders in `.env.example`; never log secrets; tests secret-free
  - Expanded `.env.example` stubs: Slack (bot/app/signing/channel), Google Calendar (future), optional LLM placeholders
  - README + architecture: Phase 2 scoped; Listener row → Scoped
- **Tests**: unchanged (15 passed expected; no listener code yet)
- **Issues / Friction**: none
- **Resilience notes**: Phase 2 must keep default pytest offline; live Socket Mode is manual smoke only. No durable stores; write gate still closed.
- **Next session plan**: TDD implement Phase 2 from locked unit test plan (red tests → handler → optional Socket Mode smoke). Do not start Confirmation or Calendar Writer until Listener acceptance is met.
- **Session status**: Phase 2 scope locked; ready for implementation weekend

---

## 2026-08-05 (weekend close)

- **Phase**: 0 done · 1 done (design + implementation + growth stance on git)
- **Saved**: `main` @ `501c2f4` clean and pushed to `origin/main` (carter-yu/cec-vivisystem)
- **Tests last known**: 15 passed
- **Do not start next**: Listener, Calendar, Confirmation, or LLM parser until Phase 2 scope is decided
- **Next session plan**: Decide Phase 2 scope only (grow from need). Candidates: Slack Listener *or* calendar read-only + confirmation path — not LLM by default
- **Session status**: Closed for this weekend

---

## 2026-08-05 (growth stance)

- **Phase**: 1 complete; docs clarification only (no code behavior change)
- **Completed**:
  - Recorded explicit stance: **grow from need**; do not pre-schedule an LLM parser phase
  - Architecture §4.4 / §4.4.1: rules are current strategy; LLM optional later behind same contract + ADR
  - Phase 1 doc + `parser.py` module note aligned
- **Tests**: unchanged (15 passed expected)
- **Next session plan**: Decide Phase 2 scope from need (e.g. Listener or calendar path)—not LLM parser by default
- **Session status**: Stance documented for final review on git

---

## 2026-08-05 (implementation)

- **Phase**: 1 – Natural Language Parser (implemented)
- **Completed**:
  - `src/cec_vivisystem/models.py` — `IntentType`, `Confidence`, `ParseResult` (Phase 1 contract)
  - `src/cec_vivisystem/parser.py` — offline rule/heuristic `parse(message, *, now=, correlation_id=)`; weekday policy documented in module
  - Boundary logs: `parse_started` / `parse_completed` with `component`, `outcome`, `intent_type`, `duration_ms`, preview (structlog event name = boundary event)
  - `tests/test_parser.py` — F1–F5 create-event, 2 clarification, 2 unknown, contract + garbage + log boundary
  - CLI smoke: `uv run python -c "from cec_vivisystem.parser import main; main()"`
  - Acceptance criteria checked in `phases/phase-1-parser.md`; architecture Parser → Done
- **Tests**: 15 passed (12 parser + 3 hello); ruff clean
- **Issues / Friction**: structlog reserves keyword `event` — use event name as the log message positional arg
- **Resilience notes**: No network/LLM; parse never raises on garbage; class A logs only (no durable parse store)
- **Next session plan**: Decide Phase 2 scope only (do not implement Listener/Calendar until scope is explicit)
- **Session status**: Phase 1 implementation complete

---

## 2026-08-05

- **Phase**: 1 – scope + unit test plan + logging/retention standard (no swarm implementation)
- **Completed**:
  - Decided Phase 1: first swarm component = offline **Natural Language Parser** (create-event intent)
  - Wrote [phases/phase-1-parser.md](phases/phase-1-parser.md) with goal, contract fields, acceptance criteria, and explicit out-of-scope
  - **Locked unit test plan** in the same phase doc: ~11–12 tests in `tests/test_parser.py`
    - F1–F5 create-event fixtures (mixed Canto/English; fixed `now=2026-08-08 12:00 Asia/Hong_Kong`)
    - 2 needs_clarification, 2 unknown/empty, contract + no-raise + log boundary
    - Explicit non-tests: Slack, Calendar, LLM mocks, fuzzing
  - **Logging & retention standard** ([docs/logging-and-retention.md](docs/logging-and-retention.md), [ADR 0002](docs/decisions/0002-logging-and-retention.md)):
    - Per-component boundary log matrix (troubleshoot without replaying Slack/Calendar)
    - Retention: app logs 14d; audit 90d; pending state terminal+7d (max 30d); dead letters 30d; notes until family deletes
    - No store without purge story; soft disk caps; secrets never logged
  - **Elevated to system spine (all future phases)**:
    - [docs/unit-testing.md](docs/unit-testing.md) + [ADR 0003](docs/decisions/0003-unit-testing-standard.md)
    - Ground rules 4–5 + 12: unit tests + logging/retention mandatory; phases cannot waive
    - Resilience rewritten as permanent checklist (tests + logs + retention + calendar write gate)
    - Architecture §4.5–4.6: cross-cutting quality bars + phase document contract
    - Phase 0/1 headers + README: inherit non-waivable standards
  - Rationale: highest leverage before Listener/Calendar; fully offline so OAuth/Slack cannot burn the 1–2h session; contract-first for later components
- **Tests**: unchanged in code (plan only; Phase 0 still 3 passed)
- **Issues / Friction**: none
- **Resilience notes**: Unit testing + log/retention are now permanent system rules, not Phase 1 one-offs. Phase 1 still forbids calendar writers; parser ships with happy + failure paths and boundary logs (class A only).
- **Next session plan**: TDD implement Phase 1 — red tests from unit test plan, then green parser with logging fields from the standard. Do not start Slack or Google Calendar until Parser acceptance criteria are met.
- **Session status**: Scope + tests + log/retention policy locked as **system-wide** standards; ready for implementation weekend

---

## 2026-07-26

- **Phase**: 0 – Environment & Foundations
- **Completed**:
  - Project structure and core docs (philosophy, ground rules, architecture, resilience, ADR 0001)
  - Python 3.12 pinned; `uv` + lock file; `ruff` and `pytest` configured
  - Hatchling package install for `src/cec_vivisystem`
  - `src/cec_vivisystem/logging.py` (structlog) and `hello.py` proof-of-life module
  - `.env.example` present
  - Three unit tests passing; structured logging verified
- **Tests**: 3 passed
- **Issues / Friction**: Package import / editable install friction early on (resolved with hatchling build config)
- **Resilience notes**: Logging and tests in place from day one; no calendar writers yet
- **Repo**: Pushed to `carter-yu/cec-vivisystem` on GitHub
- **Next session plan**: Decide Phase 1 scope only; do not implement swarm components until that decision is explicit
- **Session status**: Closed for this weekend

---

## Template
### YYYY-MM-DD
- **Phase**:
- **Completed**:
- **Tests**:
- **Issues / Friction**:
- **Resilience notes**:
- **Next session plan**:
