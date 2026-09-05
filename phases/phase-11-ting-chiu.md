# Phase 11 – Parser 聽朝 (tomorrow morning)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  
- [Phase 1 Parser](phase-1-parser.md) · [Phase 3 expansion](phase-3-parser-expansion.md) · [Phase 9 aliases](phase-9-parser-aliases.md) · [Phase 10 titles](phase-10-parser-titles.md)  

Parser-only. Same `parse` → `ParseResult` contract. **Does not** change Writer, Listener, overlap, or the Reader. **No LLM.**

## Goal

Treat **聽朝** as tomorrow (the morning of the next calendar day in `Asia/Hong_Kong`), so live family lines with 聽朝 + a clock become `create_event` instead of `needs_clarification` (missing `start`).

**梓梵 is Cedric.** Canonical participant remains `Cedric`.

Default pytest: no network, no tokens, no LLM.

## Why this phase

Mini launchd is up. Live `#family-plans` line (2026-09-05):

`聽朝11點帶梓梵去MS Wong 度上堂`

→ `needs_clarification`, `missing_fields=['start']`. Title (MS Wong) and 梓梵→Cedric already work. **聽朝** is not in the tomorrow set (`明天` / `tomorrow` / `聽日`). 11點 is a time without a date.

聽朝 = tomorrow morning in Hong Kong Cantonese. Same date as 聽日; bare `N點` already keeps the hour (11點 → 11:00).

## Time box

1–2 hours. Green the locked fixtures and stop.

## In Scope

Keep `parse(message, *, now=, correlation_id=) -> ParseResult`.

1. **聽朝** → calendar day after `now` (same rule as 聽日 / 明天 / tomorrow).
2. 聽朝 is a create/schedule signal.
3. Live line above: `create_event`; title Miss Wong 堂; start tomorrow 11:00 HKT; participants include **Cedric**.
4. Document 聽朝 in `parser.py` module docstring.

Do **not** add 聽晚 / 今朝 / other day-parts this session.

## Out of Scope

| Item | Why later |
|------|-----------|
| Writer / Listener / overlap / reader edits | Parser is the date seam |
| 聽晚, 今朝, 後日 | Not in the live line |
| Guessing PM for 聽朝 + afternoon hours | 聽朝11點 is morning; bare N點 policy unchanged |
| Extra titles (買餸, playdate, 迪士尼) | Not this live line |
| LLM | Rules first |
| Mini git pull / kickstart | Operator |

## Unit test plan (locked for implementation)

Authoritative Phase 11 test decision. Implements [unit-testing.md](../docs/unit-testing.md).

### Layout

| Path | Role |
|------|------|
| `tests/test_parser.py` | M1–M3 + existing F/L/A/T/Q stay green |
| Other suites | W5 = full pytest |

### Determinism

`FIXED_NOW = 2026-08-08 12:00 Asia/Hong_Kong`. No network, no LLM.

### Fixtures / cases

| ID | Phrase | Expect |
|----|--------|--------|
| **M1** | `聽朝11點帶梓梵去MS Wong 度上堂` | `create_event`; title Miss Wong 堂; start **2026-08-09 11:00 HKT**; participants include **Cedric**; `missing_fields` empty |
| **M2** | A1 `聽日9點，梓梵游水` | unchanged create 游泳 + Cedric |
| **M3** | `聽朝帶梓梵去MS Wong 堂` | `needs_clarification`; missing `start` (date without clock); must not invent a time; 梓梵 still Cedric if participants filled |
| **A6** | `梓梵同朋友一齊砌積木，好開心。` | still `unknown` |
| **W5** | full `pytest` | previous suites green |

Named tests:

- `test_parse_ting_chiu_ms_wong_zifan` → M1  
- `test_parse_ting_chiu_missing_clock` → M3  

M2 / A6 are existing tests if still green.

### Non-tests

- Slack / Socket Mode
- Writer / overlap / reader code changes
- Live Google
- 聽晚 / 今朝
- Extra titles
- LLM

### Minimum green bar

2 new tests + full suite green.

## Logging & retention applicability

| Data | Class | Phase 11 action |
|------|-------|-----------------|
| Parser logs | **A** | Existing `parse_started` / `parse_completed`; 14d file purge |
| Parse results | — | Not persisted |
| Secrets | — | Unchanged |

No new store.

## Acceptance Criteria

- [x] This file locked before implementation
- [x] M1 is `create_event` with Miss Wong 堂, **Cedric**, 2026-08-09 11:00 HKT
- [x] M3 needs a clock; no invented time
- [x] 梓梵 → Cedric on M1 (and existing A1)
- [x] 聽日 fixtures unchanged
- [x] Writer / Listener / overlap source **unchanged** this session
- [x] No LLM; default pytest offline
- [x] `uv run pytest` and `ruff check .` clean
- [x] Architecture, README, `PROGRESS.md`, parser docstring updated
- [ ] Live Slack smoke **not** required (stretch: Mini `git pull` + kickstart + repost M1)

## Suggested session order (TDD)

1. Lock this document.  
2. Red tests M1, M3.  
3. 聽朝 in tomorrow + create-signal until green.  
4. Docs; `pytest` + `ruff`.  
5. Stop before 聽晚, extra titles, Writer, or LLM.

## Success definition

Phase 11 is **done** when `聽朝11點帶梓梵去MS Wong 度上堂` is a confirmable `create_event` (Miss Wong 堂, Cedric, tomorrow 11:00 HKT).

## Explicit non-goal

Do not treat every 朝 as a date. Do not drop 梓梵→Cedric. Do not start an LLM parser.
