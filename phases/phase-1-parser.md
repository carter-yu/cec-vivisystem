# Phase 1 – Natural Language Parser (First Swarm Component)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

The unit test plan below is the Phase 1 **specialization** of the system unit-testing standard. Logging fields below specialize the system logging standard. Future phases must write their own specialization the same way—not skip these standards.

**Goal**  
Ship the first real swarm component: a pure, offline **Parser** that turns family natural-language messages (Cantonese + English, often mixed) into a structured intent. No Slack, no Google Calendar, no network.

**Why this phase (decision)**  
Phase 0 only proved the environment. The architecture diagram has many components; ground rules forbid building the full swarm in advance. Phase 1 must be:

1. **The highest-leverage first domain piece** — almost every later path (Listener → Parser → Confirmation → Calendar Writer) needs structured intent.
2. **Fully offline and testable** — Google OAuth / Slack tokens routinely burn a whole 1–2 hour session; we refuse that as the first capability.
3. **Replaceable and contract-first** — a clear input/output contract so Listener and Confirmation Guardian can plug in later without rewriting the parser.
4. **Useful even before integrations** — fixture-driven tests and a CLI smoke path prove real family phrasing works before any external system exists.

**Time box**  
1–2 hours (one weekend session). If the session ends early, leave a working package + tests + docs, not a half-wired external API.

## In Scope

### Component: `Parser`
- **Input**: a single message string (and optional metadata such as sender id / timestamp for logging only).
- **Output**: a structured intent object (see contract below), or an explicit “could not parse / needs clarification” result.
- **Language**: mixed Cantonese + English family speech (not formal written Chinese only).
- **Intent focus for Phase 1**: **create calendar event** proposals only (the primary family need). Other intents (query free time, cancel, notes) return a clear unsupported/unknown shape rather than being fully implemented.

### Structured intent contract (minimum fields)
Documented in code (typed model) and covered by tests:

| Field | Notes |
|-------|--------|
| `intent_type` | e.g. `create_event`, `unknown`, `needs_clarification` |
| `title` | short event title if present |
| `start` / `end` | datetime or date+time; timezone-aware when possible |
| `all_day` | boolean |
| `location` | optional |
| `participants` | optional list (e.g. Cedric, Elaine) |
| `raw_text` | original message |
| `confidence` | simple signal (e.g. high / medium / low or float) |
| `missing_fields` | what is needed if clarification is required |
| `notes` | free-text remainder |

Exact type names may vary; the fields and semantics above are the acceptance contract.

### Implementation constraints
- Pure functions / isolated module under `src/cec_vivisystem/` (e.g. `parser.py` + small models module). No Slack/Calendar clients.
- Unit tests: happy path **and** at least one failure / ambiguous path (resilience standards).
- Structured logging at the parse boundary per [docs/logging-and-retention.md](../docs/logging-and-retention.md): `component=parser`, `parse_started` / `parse_completed` (or equivalent), `intent_type`, `outcome`, `duration_ms`, input length or short preview — **not** secrets. Optional `correlation_id`.
- Phase 1 stores no durable parse history (app logs only = retention class **A**, 14 days once file logs exist). Do not build audit DB or purge cron in this phase.
- Optional thin CLI or `python -c` proof-of-life that parses 2–3 fixture phrases and logs the result (same spirit as Phase 0 `hello`).
- Prefer deterministic rules / date parsing first; **do not** require an LLM API key for Phase 1 acceptance.
- **LLM is optional and demand-driven**, not a scheduled follow-on phase. A later session *may* add an LLM-backed (or hybrid) strategy **behind the same `parse` → `ParseResult` contract** only if real family phrases outgrow rules; write an ADR if that lands. See architecture §4.4 / §4.4.1.

### Docs / process
- Update `docs/architecture.md` component table: Parser → In progress / Done as appropriate.
- ADR only if a non-obvious choice appears (e.g. date library, model shape, or LLM vs rules). Skip ADR for “we built a parser.”
- Update `PROGRESS.md` at session end.
- Link this file from `README.md` when implementation starts or completes.

## Unit test plan (locked for implementation)

This section is the **authoritative Phase 1 test decision** (fixtures and expected outcomes).  
It **implements** the system-wide [Unit Testing Standard](../docs/unit-testing.md); it does not replace it.  
Implementation should make these tests pass; do not expand coverage mid-session unless a fixture is broken by a genuine contract bug.

### Layout

| Path | Role |
|------|------|
| `tests/test_parser.py` | All Phase 1 parser unit tests |
| `tests/fixtures/parser_phrases.py` (optional) | Shared fixture strings + expected shapes if `test_parser.py` grows noisy |

Keep Phase 0 tests (`tests/test_hello.py`) unchanged.

### Determinism rules

1. **Fixed reference time**: every test that involves relative dates (“星期六”, “tomorrow”) calls `parse(..., now=FIXED_NOW)` where  
   `FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))`  
   (Saturday noon HKT). Relative weekday math must be tested against this instant, not wall clock.
2. **Default timezone**: `Asia/Hong_Kong` for family local time; start/end in results must be timezone-aware when a time is known.
3. **No network, no LLM, no filesystem side effects** in unit tests.
4. **Public API under test**: `parse(message: str, *, now: datetime | None = None) -> ParseResult` (exact name may match models module; one public entry point only).

### Intent outcomes to assert

Use the contract enums/strings exactly as implemented; tests must distinguish:

| Outcome | When |
|---------|------|
| `create_event` | Enough signal for a calendar create proposal (title + usable start at minimum) |
| `needs_clarification` | Looks like create-event but missing required time/date (or other required field) |
| `unknown` | Not a create-event request, or unusable noise |

`raw_text` must always equal the input message (strip-only normalization is allowed if documented; prefer exact original string).

### Required fixture phrases (≥5 create-event family phrases + failure paths)

These five are the **minimum create-event-oriented fixtures** (acceptance criterion). Expected fields below are the bar for “green”; parser may fill more, but must not contradict them.

| ID | Input (`message`) | Expected `intent_type` | Must assert |
|----|-------------------|------------------------|-------------|
| F1 | `星期六下午3點帶 Cedric 去游泳` | `create_event` | title mentions swim/游泳; start = 2026-08-08 15:00 HKT; participants include Cedric; `all_day` is false; confidence not low |
| F2 | `Sunday 10am pediatrician for Cedric` | `create_event` | title relates to pediatrician; start = 2026-08-09 10:00 HKT; participants include Cedric |
| F3 | `下星期三 Elaine 睇牙醫 2:30pm` | `create_event` | start = 2026-08-12 14:30 HKT; participants include Elaine; title relates to dentist/牙醫 |
| F4 | `Add family dinner Friday 7pm at home` | `create_event` | start = 2026-08-14 19:00 HKT; location relates to home; English-only phrase works |
| F5 | `明天全日 Cedric 學校 holiday` | `create_event` | `all_day` true; start date = 2026-08-09 (HKT calendar day); title relates to school/holiday |

Relative date anchors for `FIXED_NOW` = Sat 2026-08-08 12:00 HKT:

- 星期六 / this Saturday → 2026-08-08  
- Sunday → 2026-08-09  
- 明天 → 2026-08-09  
- 下星期三 → 2026-08-12  
- Friday → 2026-08-14  

If the implementation’s weekday policy differs (e.g. “星期六” after noon means *next* Saturday), **document it in one place** and adjust only F1’s expected date in tests—do not invent per-test policies.

### Named unit tests (implement these)

**A. Happy path – create_event (one test per fixture F1–F5, or parametrize)**

- `test_parse_create_event_cantonese_saturday_swim` → F1  
- `test_parse_create_event_english_sunday_pediatrician` → F2  
- `test_parse_create_event_mixed_next_wednesday_dentist` → F3  
- `test_parse_create_event_english_friday_dinner` → F4  
- `test_parse_create_event_all_day_tomorrow_school_holiday` → F5  

Shared assertions for create_event success:

- `intent_type == create_event`
- `raw_text` preserved
- `start` is timezone-aware
- `missing_fields` empty (or equivalent)
- `title` non-empty
- `confidence` is high or medium (not “give up” low) for these clear phrases

**B. Needs clarification (ambiguous / incomplete create)**

- `test_parse_needs_clarification_missing_time`  
  - Input: `幫我 book 游泳`  
  - Expect: `needs_clarification`; `missing_fields` includes something time/date related; `raw_text` preserved; must **not** invent a fake `start`

- `test_parse_needs_clarification_missing_what`  
  - Input: `星期六下午3點`  
  - Expect: `needs_clarification`; missing title/what (or equivalent); if start is filled that is OK; must not claim full successful create without a title

**C. Unsupported / unknown**

- `test_parse_unknown_not_create_event`  
  - Input: `今日天氣點呀`  
  - Expect: `intent_type == unknown` (or non-create); no fabricated calendar `start` required for success path

- `test_parse_unknown_empty_or_whitespace`  
  - Input: `   `  
  - Expect: `unknown` (or explicit invalid); no crash

**D. Contract / resilience**

- `test_parse_result_has_contract_fields`  
  - Any successful parse (use F1): result exposes the Phase 1 contract fields (`intent_type`, `title`, `start`, `end`, `all_day`, `location`, `participants`, `raw_text`, `confidence`, `missing_fields`, `notes`) — via attributes or dict; presence matters more than every field being non-None.

- `test_parse_does_not_raise_on_garbage`  
  - Input: random punctuation / emoji-only e.g. `???!!! 😅`  
  - Expect: returns a result (`unknown` or `needs_clarification`), **never** uncaught exception

- `test_parse_logs_boundary` (lightweight)  
  - Call `parse` on F1 with logging configured (autouse fixture already exists)  
  - Assert at least that parse completes; optional: caplog/structlog capture that a boundary event was emitted  
  - Do **not** require brittle full log message equality

### Parametrize vs separate tests

Prefer `@pytest.mark.parametrize` for F1–F5 **if** expected fields stay table-driven in one place; separate named tests are fine if clearer in a 1–2h session. Either style satisfies acceptance as long as all IDs F1–F5 and B–D above exist.

### Explicit non-tests (Phase 1)

Do **not** write unit tests for:

- Slack payload shapes / event envelopes  
- Google Calendar API responses  
- Confirmation timeouts / proposal wording  
- LLM provider mocks  
- Persistence  
- End times inferred from duration unless a fixture explicitly includes an end (F1–F5 do not require `end`)  
- Perfect Cantonese NLP / every dialect variant  
- Property-based fuzzing (out of time box)

### Minimum green bar (count)

| Category | Min tests |
|----------|-----------|
| Create-event fixtures F1–F5 | 5 |
| Needs clarification | 2 |
| Unknown / empty | 2 |
| Contract + no-raise + log boundary | 2–3 |
| **Total** | **~11–12** |

Phase 0’s 3 hello/logging tests remain; full suite ≈ 14–15 tests.

### Implementation order (TDD)

1. Add `ParseResult` + `parse()` stubs returning `unknown`.  
2. Add `tests/test_parser.py` with the cases above (red).  
3. Implement only enough rules/heuristics to green the suite.  
4. Stop when green + ruff clean — no extra phrases mid-session.

---

## Acceptance Criteria

- [x] Parser module exists with an explicit public API (e.g. `parse(message: str, ...) -> ParseResult`)
- [x] Typed structured result for success, unknown, and needs-clarification cases
- [x] At least **5** fixture phrases covering mixed Cantonese/English family-style create-event requests (committed as tests or test data)
- [x] Unit tests match the **Unit test plan** above (F1–F5 + clarification + unknown + contract/resilience)
- [x] `uv run pytest` passes; `uv run ruff check .` clean on touched code
- [x] Structured log line on parse attempt / result
- [x] No network calls; no Slack or Google credentials required to develop or test
- [x] `PROGRESS.md` updated; architecture status row for Parser updated
- [x] System remains runnable from a cold `uv sync` (Phase 0 still holds)

## Out of Scope

| Item | Why later |
|------|-----------|
| Slack Listener | Needs tokens, event subscription, and a host process; depends on Parser contract |
| Google Calendar read/write | OAuth and write risk; Writer only after Confirmation Guardian |
| Confirmation Guardian / Proposal Agent | Need a real parse result in production flow first |
| Availability Checker | Depends on Calendar API |
| Life Notes Keeper, Reminder Agent | Separate vertical slices after calendar path is real |
| LLM-required parsing | Avoids API key + non-determinism as Phase 1 gate; optional later behind same contract |
| Persistence / database | Not needed for pure parse |
| Production deploy / always-on process | Still weekend-local development |
| Full multi-intent NLU | Only create-event + explicit unknown/clarification |

## Suggested session order (implementation weekend)

1. Define `ParseResult` / intent models + public `parse()` signature (`now=` for determinism).
2. Add `tests/test_parser.py` from the **Unit test plan** (red).
3. Implement minimal rule/heuristic parser good enough for F1–F5 + B–D.
4. Wire structured logging; optional CLI / `python -c` smoke on 2–3 fixtures.
5. Lint, pytest, update architecture + `PROGRESS.md`.

## Success definition

Phase 1 is **done** when a family-style message can be turned into a structured create-event intent (or an explicit clarification/unknown result) entirely offline, with tests and logs, and zero external services. That contract becomes the stable seam for later phases (likely Listener *or* Calendar read-only + confirmation path — decide only when scoping the next phase from need). Replacing rules with an LLM is **not** required for Phase 1 success and is **not** assumed as Phase 2.

## Explicit non-goal

Do not implement “half of Slack + half of Calendar” in the same weekend as the parser. One component, one contract, green tests.
