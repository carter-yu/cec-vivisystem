# Phase 5B – Slack Wiring for LifeNotesKeeper

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

This phase wires the existing **Listener** into the existing **LifeNotesKeeper** Option A core ([Phase 5A](phase-5a-life-notes-keeper.md)). It does **not** waive system standards. It does **not** change the Parser contract or the life-note store.

## Goal

Messages posted to Slack `#family-life-notes` are accepted by the Listener, passed to `create_life_note(...)`, and persisted as `status="raw"` with exact `raw_text` plus source metadata (`channel`, `message_id`, `user`).

Messages in `#family-plans` keep the existing parse-reply path. The two channels must not mix.

## Why this phase (decision)

Phase 5A shipped a testable offline keeper. Live friction (2026-08-28): a real message in `#family-life-notes` was logged `wrong_channel` because the Listener still allowlisted only the plans channel. Life notes cannot be captured in real use until dispatch exists.

This is the 5A Slack **stretch**, now its own thin phase so the weekend stays bounded.

## Time box

1–2 hours. Prefer green offline tests + thin dispatch over polished UX. A short acknowledgement (`已記低`) is in scope if it stays small; omit rather than block acceptance.

## Config choice (locked)

**Two named env vars** — not a comma-separated allowlist, not the old generic `SLACK_ALLOWED_CHANNEL_ID`.

| Env var | Channel | Path |
|---------|---------|------|
| `SLACK_FAMILY_PLANS_CHANNEL_ID` | `#family-plans` | Existing parse → reply (Phase 2) |
| `SLACK_LIFE_NOTES_CHANNEL_ID` | `#family-life-notes` | `create_life_note` → store (this phase) |

Rationale: the channels have different downstream behaviour. A shared allowlist would send life notes through the parser. Names/placeholders only in `.env.example` (ground rule 13). Real ids stay in local `.env`.

`load_slack_config` must read **both**. Missing or empty `SLACK_LIFE_NOTES_CHANNEL_ID` is a `ConfigError` naming the key only (no secret values).

`SlackConfig.allowed_channel_ids` remains the **plans** channel set (Phase 2 field). Life-notes id is a separate field (`life_notes_channel_id`). Dispatch branches on channel id; it does not put both ids in the parse allowlist.

## In Scope

### Listener dispatch

Extend `process_slack_message_event` (and Socket Mode runner) so that after normalize + bot/subtype filters:

1. If `channel_id == life_notes_channel_id` → life-notes path (`create_life_note`). Do **not** call `parse`.
2. Else if `channel_id` is in plans `allowed_channel_ids` → existing `handle_inbound` parse-reply. Do **not** create a life note.
3. Else → `ignored` / `wrong_channel`. No note, no parse required.

Optional kwargs (defaults keep Phase 2 tests green):

- `life_notes_channel_id: str | None = None`
- `life_notes_store: LifeNotesStore | None = None`
- `now=` forwarded into `create_life_note`

When `life_notes_channel_id` is omitted (existing unit tests), behaviour is unchanged: only `allowed_channel_ids` are accepted.

### Capture contract (reuse Phase 5A)

- `raw_text` = exact Slack message text
- `status="raw"`
- `source.channel` = Slack channel id
- `source.message_id` = Slack `client_msg_id` or `ts` (`InboundMessage.slack_event_id` / `ts`)
- `source.user` = Slack user id
- `correlation_id` propagated Listener → keeper

### Reply (thin)

On successful store: `outcome=replied`, `reply_text` = `已記低` (family-readable). Socket Mode already posts `reply_text` on `REPLIED`.  
`parse_result` stays `None` on this path.

If acknowledgement is dropped as stretch, still persist the note; document the skip. Prefer shipping the one-line ack.

### Empty / whitespace in life-notes channel

Controlled: **no** empty note stored; Listener **does not crash**. Locked outcome: `ignored` / `empty_text` (same as Phase 2 L4), checked after channel accept, **before** `create_life_note`.

If `create_life_note` raises `LifeNoteError` anyway, catch it and return `failed` with `error_type` — still no crash, still no stored empty note.

### Live Socket Mode

Pass `life_notes_channel_id` and a `JsonDirLifeNotesStore` (`data/life_notes/`, already gitignored) into `process_slack_message_event`. Default pytest never constructs the live client.

### Implementation constraints

- Extend Listener only; **reuse** `create_life_note` / `InMemoryLifeNotesStore` / `JsonDirLifeNotesStore`.
- **No** structured extraction, LLM, DB, vector store, Calendar Writer, Confirmation Slack yes/no, Parser contract change, or auto-purge of notes (class F).
- Structured logs: Listener boundary (`message_received`, `dispatch_succeeded` / `dispatch_failed` with `next_component=life_notes`) plus existing keeper logs (`component=life_notes`).
- Secrets: env names only; never log tokens.

### Docs / process

- This file locked **before** code.
- Update `architecture.md` §2/§3/§5: Listener dispatch to LifeNotesKeeper is done.
- Update `PROGRESS.md`, README, `.env.example` comment (placeholder still empty).
- Mark Phase 5A Slack stretch as completed by 5B (do not rewrite 5A history).

## Out of Scope

| Item | Why later |
|------|-----------|
| Structured extraction (people / emotion / location / multi-event split) | Later enrichment on stored `raw_text` |
| LLM | Grow from need + ADR |
| Database / vector store | File store from 5A is enough |
| Calendar Writer | Separate calendar path |
| Confirmation Slack yes/no (Phase 4b) | Do not combine in this session |
| Parser contract change | Life notes skip the parser |
| Auto-purge of life notes | Class F — until family deletes |
| Query / reflect commands | Later increment |

## Unit test plan (locked for implementation)

Authoritative Phase 5B test decision. Implements [unit-testing.md](../docs/unit-testing.md).

### Layout

| Path | Role |
|------|------|
| `tests/test_listener.py` | S1–S5 (+ config) on the Listener dispatch path |
| `tests/test_life_notes.py` | Unchanged Phase 5A LN1–LN5 |
| In-memory `LifeNotesStore` | Injected; never hit real Slack or `data/life_notes/` in default pytest |

Keep Phase 0–5A suites green (S6).

### Determinism

1. Injectable `now=` (reuse `FIXED_NOW = 2026-08-08 12:00 Asia/Hong_Kong` if asserting timestamps).
2. Injectable `life_notes_store` (`InMemoryLifeNotesStore`).
3. Fake Slack event dicts (same shape as Phase 2 `_user_message`).
4. No network, no tokens, no LLM in default suite.

### Fixtures / cases

| ID | Scenario | Expect |
|----|----------|--------|
| S1 | User message in life-notes channel | `create_life_note` path runs; `raw_text` exact; note in store; `status="raw"`; `parse_result is None` |
| S2 | User message in `#family-plans` (plans allowlist) with a store injected | Existing parse-reply; **no** life note created |
| S3 | Wrong channel / bot / subtype | `ignored`; no note |
| S4 | Empty or whitespace text in life-notes channel | Controlled ignore or failure; store empty; **no** uncaught exception |
| S5 | Source metadata from Slack event | Stored `source.channel`, `source.message_id` (event id or `ts`), `source.user` |
| S6 | Previous Phase 2 + 5A suites | Stay green (full `uv run pytest`) |

### Named tests (implement these)

- `test_life_notes_channel_stores_raw_text` → S1  
- `test_family_plans_does_not_create_life_note` → S2  
- `test_wrong_channel_bot_subtype_do_not_create_life_note` → S3  
- `test_empty_life_notes_text_does_not_store` → S4  
- `test_life_note_source_metadata_from_slack` → S5  
- Config: `load_slack_config` reads `SLACK_LIFE_NOTES_CHANNEL_ID`; missing key named in `ConfigError` (extend L6, no secret values)

Realistic S1 text (not a calendar phrase), e.g. `梓梵今日喺學校同朋友一齊砌積木，好開心。`

### Non-tests (this phase)

- Live Slack Socket Mode (manual smoke only)
- Structured enrichment, LLM, Calendar, Confirmation yes/no
- Persistence format of JSON files (already covered in 5A)
- Multi-workspace / extra channels

### Minimum green bar (count)

| Category | Min tests |
|----------|-----------|
| S1 happy path | 1 |
| S2 isolation from plans | 1 |
| S3 ignore filters | 1 (may cover wrong-channel + bot in one test) |
| S4 empty text | 1 |
| S5 source metadata | 1 |
| Config both channel env vars | 1 (may fold into existing L6) |
| **Total new** | **~5–7** |

S6 is the existing suite, not extra cases.

## Logging & retention applicability

| Data | Class | Phase 5B action |
|------|-------|-----------------|
| Listener structured logs | **A** | `message_received`; `dispatch_succeeded` / `dispatch_failed` with `next_component=life_notes`; `correlation_id`, channel/user ids, preview/length — **never** tokens |
| Life-notes keeper logs | **A** | Existing `note_create_started` / `note_written` / `note_create_failed` (`component=life_notes`) |
| Note bodies on disk | **F** | Unchanged: until family deletes; no auto-purge; `data/life_notes/` gitignored |
| Slack tokens / channel ids in `.env` | secrets / local | Names in `.env.example` only |

No new store class. No new purge story (class F already documented in 5A).

## Acceptance Criteria

- [x] Phase doc (this file) locked before implementation
- [x] Listener accepts `#family-life-notes` via `SLACK_LIFE_NOTES_CHANNEL_ID`
- [x] Life-notes messages call existing `create_life_note`; `raw_text` exact; `status="raw"`
- [x] `#family-plans` parse-reply unchanged; no life note from that channel
- [x] Wrong channel / bot / empty handled without crash; no empty notes
- [x] Source metadata stored: channel, message id/ts, user
- [x] Unit tests S1–S5 (or equivalent named tests) pass; S6 full suite green
- [x] Default pytest offline, secret-free, no LLM
- [x] Boundary logs on Listener + keeper
- [x] `.env.example` placeholders only; architecture, README, `PROGRESS.md` updated
- [x] `uv run pytest` and `ruff check .` clean
- [x] No LLM, no DB, no Calendar Writer, no Phase 4b, no Parser contract change

### Manual smoke checklist (opt-in; not part of pytest)

1. Local `.env` has `SLACK_FAMILY_PLANS_CHANNEL_ID` and `SLACK_LIFE_NOTES_CHANNEL_ID` (never commit).
2. Bot invited to `#family-life-notes`.
3. `uv run python -c "from cec_vivisystem.listener import main; main()"`
4. Post a life note in `#family-life-notes` → expect `已記低` (if shipped) and a JSON file under `data/life_notes/`.
5. Post a plan in `#family-plans` → parse summary only; no new life note.
6. Confirm `logs/listener-*.log` shows `dispatch_succeeded` `next_component=life_notes` for (4), not `wrong_channel`.

## Suggested session order (TDD)

1. Lock this document (done when committed to the working tree).  
2. Red tests S1–S5 in `tests/test_listener.py`.  
3. Extend `SlackConfig` / `load_slack_config` / `process_slack_message_event` dispatch; reuse `create_life_note`.  
4. Socket Mode: pass life-notes channel + JSON store.  
5. Thin ack `已記低` if still in time box.  
6. Docs + `PROGRESS.md`; `pytest` + `ruff`.

## Success definition

A family message in `#family-life-notes` is stored as a durable raw life note with exact original text. A family message in `#family-plans` is still parsed as a calendar proposal and does **not** become a life note. Offline tests prove the split without Slack.
