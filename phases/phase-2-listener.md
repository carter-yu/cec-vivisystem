# Phase 2 – Slack Listener (Thin Intake Slice)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

The unit test plan below is the Phase 2 **specialization** of the system unit-testing standard. Logging fields specialize the system logging standard (Listener matrix). Future phases must write their own specialization the same way—not skip these standards.

**Goal**  
Ship the second swarm component: a thin **Slack Listener** that receives family messages from a designated channel, runs the existing offline **Parser**, and posts a short human-readable summary of the `ParseResult` back to Slack. No calendar, no confirmation store, no LLM.

**Why this phase (decision)**  
Phase 1 proved structured intent offline. Real family use is still blocked: there is no intake channel. Phase 2 must be:

1. **The next thin vertical slice that unblocks real use** — family can type in Slack and see that the system understood (or needs clarification).
2. **Contract-preserving** — call `parse(...) -> ParseResult` only; do not change the Phase 1 contract unless a bug forces it.
3. **Externally bounded** — Slack I/O at the edges with injectable fakes; default `pytest` stays offline and secret-free.
4. **Write-gate still closed** — no Google Calendar create/update/delete (ground rule 6); Confirmation Guardian is a later phase.

**Time box**  
1–2 hours (one weekend session). If the session ends early, leave a working package + tests + docs and a documented manual smoke path, not a half-wired OAuth calendar path.

**Secrets**  
Slack tokens and secrets live only in local `.env` (ground rule 13). Unit tests never require real credentials.

## In Scope

### Component: `Listener` (Slack)

- **Input**: a Slack message event (or a normalized internal message model derived from one).
- **Filter**: ignore bot messages, subtypes that are not plain user text, and channels outside an allowlist (at least one configured channel id).
- **Process**: for accepted messages, call existing `cec_vivisystem.parser.parse` with the message text (and optional `now=` / `correlation_id=`).
- **Output**: post a short reply in the same channel (or thread if easy) summarizing the parse outcome—not a full calendar write proposal UI.
- **Delivery mode (locked for Phase 2)**: **Socket Mode** preferred for home Mac Mini (no public URL). Events API + HTTP endpoint is out of scope unless Socket Mode is blocked and documented as a mid-session pivot.

### Normalized message contract (minimum)

Documented in code (typed model) and covered by tests. Exact type names may vary; semantics below are the acceptance contract.

| Field | Notes |
|-------|--------|
| `text` | User message string |
| `channel_id` | Slack channel |
| `user_id` | Slack user |
| `slack_event_id` | Event id for dedupe/logging when present |
| `correlation_id` | Generated or propagated; ties logs Listener → Parser |
| `ts` / `thread_ts` | Optional; for reply threading if implemented |

### Public entry points (testable core)

Prefer a pure-ish handler so unit tests need no network:

1. **`normalize_slack_event(raw: dict) -> InboundMessage | None`**  
   Map Slack payload → inbound model, or `None` if ignore (bot, empty, wrong shape).
2. **`should_accept(message, *, allowed_channel_ids) -> bool`**  
   Channel allowlist (and any other Phase 2 filters).
3. **`handle_inbound(message, *, parse=parse, now=None) -> ListenerResult`**  
   Runs parser; returns structured result including reply text and outcome.  
   Does **not** perform Slack HTTP itself if that keeps tests simple—Slack post can be a thin adapter called by the process entrypoint.
4. **Optional process entry**: Socket Mode runner / `main` that wires env tokens + real Slack client (manual smoke only).

`ListenerResult` (minimum fields):

| Field | Notes |
|-------|--------|
| `outcome` | e.g. `replied`, `ignored`, `failed` |
| `ignore_reason` | if ignored (`bot_message`, `wrong_channel`, `empty_text`, …) |
| `parse_result` | `ParseResult` when parse ran |
| `reply_text` | human-readable summary for Slack |
| `correlation_id` | always set when work is accepted |

### Reply content (minimum bar)

Reply text must be family-readable (Cantonese+English ok in content; code/docs English). For Phase 2, English summary is enough if clearer in the time box:

- `create_event` → title, start (local), participants/location if present, confidence
- `needs_clarification` → what is missing
- `unknown` → short “could not treat as calendar create” style message

Do **not** claim the event was added to Google Calendar.

### Implementation constraints

- New module(s) under `src/cec_vivisystem/` (e.g. `listener.py` + small models); reuse `parser.parse` and logging setup.
- Credentials only from environment (see `.env.example`); never hardcode tokens.
- Unit tests: happy path **and** ignore/failure paths; mocks/fakes for Slack; no live API in default suite.
- Structured logging per Listener matrix in [logging-and-retention.md](../docs/logging-and-retention.md) §4: `component=listener`, `message_received` / `message_ignored` / `dispatch_succeeded` or `dispatch_failed`, plus auth/verify failure class if applicable. Include `correlation_id`, channel/user ids, text length or truncated preview—**never** tokens.
- Phase 2 introduces **no durable pending store** (no confirmation table). App logs only = retention class **A** (14 days once file logs exist). Optional: in-memory dedupe of recent `slack_event_id` is OK if simple; do not build a database.
- Optional dependency: official Slack SDK or minimal Web API client—record in `pyproject.toml` / lockfile when implementing; ADR only if the choice is non-obvious.
- Live Socket Mode smoke is **manual and opt-in** (real `.env`); not required for `pytest` green.

### Docs / process

- Update `docs/architecture.md` component table: Listener → In progress / Done as appropriate when implementing.
- Expand `.env.example` placeholders if new env names appear (no real values).
- Update `PROGRESS.md` at session end.
- Link this file from `README.md`.

## Unit test plan (locked for implementation)

This section is the **authoritative Phase 2 test decision**.  
It **implements** the system-wide [Unit Testing Standard](../docs/unit-testing.md); it does not replace it.  
Implementation should make these tests pass; do not expand coverage mid-session unless a fixture is broken by a genuine contract bug.

### Layout

| Path | Role |
|------|------|
| `tests/test_listener.py` | All Phase 2 listener unit tests |
| `tests/fixtures/slack_events.py` (optional) | Shared raw Slack payload dicts if tests get noisy |

Keep Phase 0/1 tests unchanged (`test_hello.py`, `test_parser.py`).

### Determinism rules

1. **No network, no real Slack, no real tokens** in unit tests.
2. **Injectable parse**: default may call real offline `parse` (preferred—proves integration with Phase 1 without network); optionally inject a stub parse for isolation of ignore-path tests.
3. **Fixed `now=`** when asserting parse-driven reply content that includes relative dates (reuse Phase 1 `FIXED_NOW` if testing end-to-end through real parser).
4. **Public APIs under test**: normalize / accept / handle_inbound (names may match implementation; one clear core path).

### Fixture payloads (minimum)

| ID | Scenario | Expected |
|----|----------|----------|
| L1 | User message in allowed channel, create-event style text (e.g. Phase 1 F1 or short English equivalent) | Accept → parse runs → `outcome=replied` → `reply_text` non-empty → `parse_result.intent_type` is `create_event` (or needs_clarification if text incomplete—prefer full create-event fixture) |
| L2 | Same as L1 but **wrong channel** | `outcome=ignored`, `ignore_reason` wrong_channel (or equivalent); **no** parse required (or parse not used for reply) |
| L3 | Bot message / `bot_id` present / `subtype=bot_message` | Ignored as bot; no reply required |
| L4 | Empty or whitespace-only text | Controlled ignore or unknown path; **no** uncaught exception |
| L5 | Malformed / missing keys payload | Controlled ignore or failure result; **no** process crash |
| L6 | Missing/invalid credentials path (mocked client or config validation) | Auth/config failure is explicit (`failed` or startup error type); no secret values in logs/assertions |
| L7 | Log boundary | handle accepted message with logging configured; completes; optional assert a listener boundary event name appeared |

### Named unit tests (implement these)

**A. Happy path**

- `test_handle_inbound_create_event_replies` → L1  
  - Assert: `outcome` replied/success; `correlation_id` set; `parse_result` present; `reply_text` mentions title or time signal (loose match OK); does **not** claim calendar write succeeded.

**B. Filters / ignore**

- `test_ignore_wrong_channel` → L2  
- `test_ignore_bot_message` → L3  
- `test_ignore_or_handle_empty_text` → L4  

**C. Resilience / contract**

- `test_malformed_payload_does_not_raise` → L5  
- `test_listener_result_contract_fields` → L1 result exposes `outcome`, `correlation_id`, and either `parse_result`+`reply_text` or ignore fields  
- `test_missing_credentials_or_auth_failure_is_explicit` → L6 (mock)  
- `test_listener_logs_boundary` → L7  

**D. Normalize (if separate function)**

- `test_normalize_user_message_extracts_text_channel_user`  
  - One realistic Slack event dict → inbound model fields populated  

(If normalize is inlined into handle, fold D into L1/L5.)

### Explicit non-tests (Phase 2)

Do **not** write unit tests for:

- Live Socket Mode connection / real Slack workspace  
- Google Calendar API  
- Confirmation timeouts / pending store  
- LLM  
- Perfect Cantonese reply wording  
- Multi-channel policy engine beyond allowlist  
- Retry/backoff matrix for Slack 429 (log failure is enough if time-boxed)  
- End-to-end always-on daemon supervision  
- Signature verification of HTTP Events API (out of scope if Socket Mode only)

### Minimum green bar (count)

| Category | Min tests |
|----------|-----------|
| Happy path L1 | 1 |
| Ignore filters L2–L4 | 3 |
| Malformed + contract + auth + log | 4 |
| Normalize (if separate) | 0–1 |
| **Total new** | **~8–9** |

Phase 0+1 remain (~15); full suite ≈ **23–24** tests when green.

### Implementation order (TDD)

1. Define inbound model + `ListenerResult` + handler stubs.  
2. Add `tests/test_listener.py` from this plan (red).  
3. Implement normalize/accept/handle + reply formatting; wire real `parse`.  
4. Add thin Socket Mode (or documented smoke) entrypoint using `.env`.  
5. Lint, pytest, update architecture + `PROGRESS.md`.

---

## Logging & retention applicability

| Data | Class | Phase 2 action |
|------|-------|----------------|
| Listener structured logs | **A** app logs | Boundary events per matrix; 14d when file rotation exists; no secrets |
| Message text | **A** only | Truncated preview at INFO preferred; full body only if needed for debug—not a durable store |
| Pending confirmations | **C** | **Not introduced** this phase |
| Audit of calendar CUD | **B** | **Not introduced** (no calendar writes) |
| Dead letters | **D** | Optional later; not required for Phase 2 acceptance |
| Slack tokens in `.env` | secrets | Local only; never logged; never committed |

No new persistent store without a purge story. Phase 2 should not create one.

---

## Acceptance Criteria

- [x] Listener module(s) exist with a testable public path (normalize/accept/handle or equivalent)
- [x] Accepted messages call existing `parse` → `ParseResult` without changing the Phase 1 contract
- [x] Reply text summarizes create_event / needs_clarification / unknown and **never** claims a calendar write
- [x] Wrong channel, bot messages, and empty/malformed input are handled without crashing
- [x] Unit tests match the **Unit test plan** above; default suite offline and secret-free
- [x] `uv run pytest` passes; `uv run ruff check .` clean on touched code
- [x] Structured listener boundary logs (`component=listener`, correlation, outcomes)
- [x] Secrets only via env; `.env.example` lists required names; no secrets in git
- [x] Documented manual smoke: Socket Mode (or pivot) with real `.env` (may be “run once” checklist in this file or PROGRESS)
- [x] Architecture Listener status updated; `PROGRESS.md` updated; README links this phase
- [x] System remains runnable from a cold `uv sync` without Slack credentials (tests still pass)

### Manual smoke checklist (opt-in; not part of pytest)

1. Create a Slack app with **Socket Mode** enabled; install to the family workspace.
2. Scopes (minimum): `chat:write`, and event subscription for `message.channels` / `message.groups` as needed for the family channel type; bot must be **invited** to the channel.
3. Copy `.env.example` → `.env` and set `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_ALLOWED_CHANNEL_ID` (never commit `.env`).
4. Run: `uv run python -c "from cec_vivisystem.listener import main; main()"` (or `uv run python -m cec_vivisystem.listener` if module path works).
5. Post a create-event style message in the allowlisted channel; expect a thread reply summarizing parse result and stating no calendar change was made.
6. Confirm wrong-channel / bot messages produce no reply (or only logs).

## Out of Scope

| Item | Why later |
|------|-----------|
| Confirmation Guardian / pending store | Separate vertical slice after intake works in real use |
| Calendar Writer / any calendar CUD | Requires confirmation (ground rule 6) |
| Calendar read-only / freebusy | Not required to unblock “message → understood” |
| Proposal Agent as full component | Phase 2 reply is a minimal summary only |
| LLM parser | Grow from need; same `parse` contract + ADR if ever |
| Events API public HTTP endpoint | Prefer Socket Mode for home host |
| Multi-workspace / multi-channel productization | One allowlisted family channel is enough |
| Production always-on deploy / launchd hardening | Still weekend-local development |
| Life Notes, Reminder Agent, Observer | Other slices |

## Suggested session order (implementation weekend)

1. Confirm Slack app: Socket Mode, bot token scopes (e.g. `chat:write`, `channels:history` / messaging as needed), app-level token; put values in local `.env` only.  
2. Models + handler stubs + red tests from unit test plan.  
3. Green handle_inbound with real offline parser + reply formatting.  
4. Thin Socket Mode runner; one manual smoke message in family channel.  
5. Lint, pytest, architecture + PROGRESS.

## Success definition

Phase 2 is **done** when a family message in the allowlisted Slack channel is accepted by the Listener, parsed by the Phase 1 parser, and answered with a clear summary—entirely without calendar writes—with offline unit tests, boundary logs, and secrets confined to local `.env`. That intake seam becomes the stable place to attach Confirmation (and only later Calendar Writer).

## Explicit non-goal

Do not implement “Slack + Confirmation + Calendar” in the same weekend. One component (Listener), one thin reply path, green tests.

## Live smoke note (2026-08-08 weekend close)

Verified on family workspace **Three of Us**, allowlisted channel **`#family-plans`**: Socket Mode receives messages, runs parser, posts thread replies; stdout + dated files under `logs/`. Intake path is **done**. Parser quality on live Cantonese is a **separate** friction item (see Phase 1 real-use note / architecture §4.4.1)—do not block Listener acceptance on perfect NLU.
