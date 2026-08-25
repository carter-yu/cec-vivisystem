# Phase 5A – LifeNotesKeeper (Option A: Reliable Raw Capture)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

This phase introduces the first slice of the **LifeNotesKeeper** component. It does **not** waive system standards.

## Goal

Ship a minimal, reliable **Life Notes** capture path:  
messages posted to the dedicated Slack channel `#family-life-notes` are stored as durable raw records (`raw_text` + basic metadata).  

No automatic structured extraction, no multi-event splitting, no LLM, no database, no vector store.

## Why this phase (decision)

Family life notes (especially about Cedric / 梓梵 school and daily moments) are a distinct need from calendar events.  

We treat this as a **parallel, independent ability** in the swarm rather than extending the calendar confirmation path.

Option A was deliberately chosen as the first increment:

- Store the original text faithfully (`raw_text`) so nothing is lost.
- Add only the minimum metadata needed for later processing.
- Defer all intelligent decomposition (people, emotion, location, multi-event split, etc.) until a later phase.
- Keep storage consistent with existing patterns (JSON files under `data/`).

This gives the family an immediately usable “memory book” while preserving full original text for future LLM or rule-based enrichment.

## Time box

1–2 hours. Prefer a green offline test suite + working store over polished Slack UX. Slack channel wiring can be stretch if needed.

## In Scope

### Components

#### 1. LifeNote data model

Minimum fields:

| Field | Notes |
|-------|--------|
| `note_id` | Stable unique id (e.g. `ln_YYYYMMDD_HHMMSS_<short>`) |
| `raw_text` | Exact original message text (required) |
| `created_at` | Timezone-aware datetime (Asia/Hong_Kong) |
| `status` | `"raw"` for this phase |
| `source` | Object containing at least `channel`, `message_id`, `user` (Slack identifiers) |
| `correlation_id` | Optional, for tracing |

No structured fields (people, emotion, location, events, etc.) in this phase.

#### 2. LifeNotesKeeper (core)

- Accept a raw message + source metadata.
- Create a `LifeNote` with `status="raw"`.
- Persist it via the store.
- Return the saved note.

#### 3. Store protocol

- `save(note: LifeNote) -> LifeNote`
- `get(note_id: str) -> LifeNote | None`
- `list_recent(*, limit: int = 50) -> list[LifeNote]` (simple, ordered by `created_at` descending)

**Production default**: JSON files under a gitignored path, e.g. `data/life_notes/`.  
**Tests**: in-memory implementation.

### Public entry points (testable core)

Prefer pure-ish APIs so default `pytest` needs no Slack:

1. `create_life_note(raw_text: str, *, source: dict, now=None, store=None) -> LifeNote`
2. Store protocol methods above

### Slack integration (stretch within Phase 5A)

- Listener recognises messages from channel `#family-life-notes`.
- For every message in that channel, call `create_life_note` and store it.
- Optional: post a short acknowledgement in the channel (“已記低”).

Not required for offline acceptance if documented as stretch.

### Implementation constraints

- New module(s) under `src/cec_vivisystem/` (e.g. `life_notes.py`, models extended in `models.py` or a dedicated file).
- **No** structured extraction / NLP / multi-event splitting.
- **No** LLM.
- **No** database or vector database.
- **No** calendar interaction.
- Structured logs with `component=life_notes` (or `life_notes_keeper`).
- Storage path must be gitignored; never commit real notes.
- Secrets unchanged (ground rule 13).

### Docs / process

- Update `architecture.md`: add LifeNotesKeeper as a parallel component.
- Update `PROGRESS.md` and README.
- Record future direction (see below).

## Out of Scope

| Item | Why later |
|------|-----------|
| Automatic field extraction (people, emotion, location, key objects, multi-event split) | Option B / later phase |
| LLM-based decomposition of `raw_text` | Explicitly deferred; will re-process stored raw notes |
| Database (SQLite / Postgres) | File store is sufficient for Option A |
| Vector database / semantic search | Only needed when reflection queries become important |
| Query / reflect commands (“list this week’s notes”) | Belongs to a later increment |
| Changing the calendar confirmation path | Life notes are independent |

## Future evolution (recorded decision)

The following are **planned follow-ons**, not part of Phase 5A:

1. **Structured enrichment** (Option B style or better)  
   Later phases may read existing `raw_text` records and produce structured fields (time, main_people, other_people, location, emotion, key_objects, event_summary). One original message may yield multiple structured events.

2. **LLM assistance**  
   When rule-based extraction is insufficient, an LLM may be introduced (behind an ADR) to decompose `raw_text`. Because we always keep the original text, we can re-process historical notes.

3. **Storage upgrade**  
   - SQLite when simple filtering by date/person becomes frequent.  
   - Vector database only when semantic search (“find all notes about toy conflicts”) is genuinely needed.

These upgrades must remain optional and additive; the `raw_text` record remains the source of truth.

## Unit test plan (locked for implementation)

### Layout

| Path | Role |
|------|------|
| `tests/test_life_notes.py` | Phase 5A unit tests |
| In-memory store fixture | Shared in `conftest` or test module |

Keep all previous phase suites green.

### Determinism

1. Injectable `now=` (fixed timestamp in Asia/Hong_Kong for tests).
2. No network, no Slack, no external services in default suite.
3. Deterministic `note_id` generation when `now=` is fixed (or assert pattern only).

### Minimum cases

| ID | Scenario | Expect |
|----|----------|--------|
| LN1 | `create_life_note` with simple Chinese text | `raw_text` preserved exactly; `status="raw"`; id and `created_at` set |
| LN2 | Source metadata is stored | `source.channel`, `source.message_id`, `source.user` present |
| LN3 | Empty or whitespace-only text | Controlled rejection / clear error (must not create empty note) |
| LN4 | Store round-trip | `save` then `get` returns equivalent note |
| LN5 | `list_recent` | Returns notes in reverse chronological order, respects limit |

### Acceptance Criteria

- [ ] `LifeNote` model and store protocol implemented
- [ ] `create_life_note` works offline and preserves `raw_text` exactly
- [ ] JSON file store under `data/life_notes/` (gitignored) + in-memory test double
- [ ] Unit tests LN1–LN5 (or equivalent) pass
- [ ] Structured logging present
- [ ] No LLM, no DB, no vector store, no calendar coupling
- [ ] `PROGRESS.md` updated
- [ ] Architecture updated to show LifeNotesKeeper as parallel component
- [ ] `uv run pytest` and `ruff check .` clean

## Suggested session order

1. Define `LifeNote` model + store protocol  
2. Write failing tests (LN1–LN5)  
3. Implement in-memory store + `create_life_note`  
4. Implement JSON directory store  
5. Add logging  
6. (Stretch) Wire Listener for `#family-life-notes`  
7. Update docs and PROGRESS.md  

## Success definition

A message posted to `#family-life-notes` (or via the pure API) results in a durable, retrievable record that contains the **exact original text** plus basic source metadata. The family can start capturing life moments immediately. All richer understanding is deferred and can be applied later to the stored `raw_text`.