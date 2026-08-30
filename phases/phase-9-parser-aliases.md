# Phase 9 – Parser Family Aliases (梓梵 / 游水 / MS Wong)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  
- [Phase 1 Parser](phase-1-parser.md) · [Phase 3 expansion](phase-3-parser-expansion.md)  

Parser-only. Same `parse` → `ParseResult` contract. **Does not** change Writer, Listener, overlap, or the Reader. **No LLM.**

## Goal

Family lines that already have a day + time become `create_event` instead of `needs_clarification` (missing title) or empty participants. Canonical outputs:

- `游水` → title `游泳`
- `MS Wong` / `MS. Wong` / `MS Wong 堂` → title `Miss Wong 堂`
- `梓梵` → participant `Cedric`

Default pytest: no network, no tokens, no LLM.

## Why this phase

Live `#family-plans` line `聽日9點，梓梵游水` parses today as `needs_clarification` with `missing_fields=['title']`. Start is already 09:00 HKT. `梓梵` is not a known participant, so Phase 8 same-person warn never fires even when the title is `游泳`. Phase 3 matches `Miss Wong` only — `MS Wong 堂` still misses title.

Grow from that intake friction. Aliases belong in the parser so downstream overlap/Writer see `Cedric` / `游泳` without extra modules.

Bare `N點` without 上午/下午 already maps 9→09:00. Do **not** invent AM/PM policy here.

## Time box

1–2 hours. Green the locked fixtures and stop.

## In Scope

### Parser heuristics only

Keep `parse(message, *, now=, correlation_id=) -> ParseResult`.

1. Title synonyms → existing canonical titles (`游泳`, `Miss Wong 堂`).
2. Participant alias `梓梵` → `Cedric` (CJK-adjacent, same style as `帶Cedric去`). Deduplicate if both appear.
3. Add `游水` (and `MS Wong` if needed) to create-signal / title keywords.
4. **Do not** add `梓梵` as a create signal by itself — life-note text must stay `unknown`.
5. Document aliases in `parser.py` module docstring.

Listener / Confirmation / Writer / overlap pick this up automatically. No code changes there.

## Out of Scope

| Item | Why later |
|------|-----------|
| Writer / Listener / overlap / reader edits | Parser is the alias seam; overlap O3 (`梓梵` on a listed Google row ≠ Cedric) stays |
| Extra nicknames, 買餸, playdate, `今日` as a date | Time-box to locked fixtures |
| Bare-hour AM/PM guessing | 9點 already 09:00 |
| LLM / hybrid parser | Rules first + ADR only if still painful |
| Setting `GOOGLE_CALENDAR_ID` | Operator; not this PR |
| Freebusy, update/delete | Later |

## Unit test plan (locked for implementation)

Authoritative Phase 9 test decision. Implements [unit-testing.md](../docs/unit-testing.md).

### Layout

| Path | Role |
|------|------|
| `tests/test_parser.py` | A1, A3, A6 + existing F/L/Q stay green |
| Other suites | W5 = full pytest, including overlap O3 |

### Determinism

`FIXED_NOW = 2026-08-08 12:00 Asia/Hong_Kong`. No network, no LLM.

### Fixtures / cases

| ID | Phrase | Expect |
|----|--------|--------|
| **A1** | `聽日9點，梓梵游水` | `create_event`; title 游泳; start 2026-08-09 09:00 HKT; participants include `Cedric`; `missing_fields` empty |
| **A2** | F1 `星期六下午3點帶 Cedric 去游泳` | unchanged create_event + Cedric |
| **A3** | `聽日上晝11點，帶梓梵去銅鑼灣上MS Wong 堂` | `create_event`; title Miss Wong 堂; start 2026-08-09 11:00 HKT; Cedric; location 銅鑼灣 if filled |
| **A4** | L1 `Miss Wong 堂` | still create_event |
| **A5** | `星期六下午3點` | still `needs_clarification` missing title |
| **A6** | `梓梵同朋友一齊砌積木，好開心。` | still `unknown` (梓梵 is not a schedule signal). Avoid `學校` in this fixture — it is already a Phase 1 create-signal for holidays |
| **A7** | `幫我 book 游泳` | still `needs_clarification` missing start |
| **W5** | full `pytest` | previous suites green, including overlap O3 |

Named tests:

- `test_parse_ting_yat_zifan_yau_seui` → A1  
- `test_parse_ms_wong_with_zifan` → A3  
- `test_parse_zifan_life_note_is_not_create` → A6  

A2 / A4 / A5 / A7 are existing tests if still green.

### Non-tests

- Slack / Socket Mode
- Writer / overlap / reader code changes
- Live Google
- Extra titles beyond the locked phrases
- LLM
- Operator `.env` calendar id

### Minimum green bar

~3 new tests + full suite green.

## Logging & retention applicability

| Data | Class | Phase 9 action |
|------|-------|----------------|
| Parser logs | **A** | Existing `parse_started` / `parse_completed`; 14d file purge |
| Parse results | — | Not persisted |
| Secrets | — | Unchanged |

No new store. No logging-standard matrix rows beyond a Phase 9 applicability note if docs are updated.

## Acceptance Criteria

- [x] This file locked before implementation
- [x] A1 is `create_event` with title 游泳, Cedric, 09:00 HKT
- [x] A3 is `create_event` with Miss Wong 堂 + Cedric
- [x] A6 life-note text stays `unknown`
- [x] Canonical names/titles only; no invented emails
- [x] F1–F5, L1–L4, list Q1–Q3 unchanged
- [x] Writer / Listener / overlap source **unchanged** this session
- [x] No LLM; default pytest offline
- [x] `uv run pytest` and `ruff check .` clean
- [x] Architecture, README, `PROGRESS.md`, parser docstring updated
- [x] Live Slack smoke **not** required (stretch: restart Listener, post A1, reply yes)

## Suggested session order (TDD)

1. Lock this document.  
2. Red tests A1, A3, A6.  
3. Title + participant heuristics until green.  
4. Docs; `pytest` + `ruff`.  
5. Stop before extra titles, Writer, or freebusy.

## Success definition

Phase 9 is **done** when `聽日9點，梓梵游水` is a confirmable `create_event` (游泳, Cedric, tomorrow 09:00) and a 梓梵 life-note line is still not a calendar create.

## Explicit non-goal

Do not alias inside overlap.py, do not invent emails, do not treat every 梓梵 mention as a plan.
