# Phase 10 – Parser Family Titles (park / playgroup / classes / errands)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  
- [Phase 1 Parser](phase-1-parser.md) · [Phase 3 expansion](phase-3-parser-expansion.md) · [Phase 9 aliases](phase-9-parser-aliases.md)  

Parser-only. Same `parse` → `ParseResult` contract. **Does not** change Writer, Listener, overlap, or the Reader. **No LLM.**

## Goal

Family lines that already have a day + time become `create_event` instead of `needs_clarification` (missing title). Canonical titles from the operator-picked set (2026-09-05):

| Surface | Canonical `title` |
|---------|-------------------|
| 公園 / playground | 公園 |
| playgroup / 遊戲班 | playgroup |
| 游水班 / swim class | 游泳 (existing Phase 9 canonical) |
| 體能班 / gym / gymnastics | 體能班 |
| 手作 / workshop / 工作坊 | 手作 |
| 商場 / mall | 商場 |
| 生日會 / birthday party | 生日會 |
| 打針 / 打疫苗 / vaccine | 打針 |

Default pytest: no network, no tokens, no LLM.

## Why this phase

Phase 9 closed 梓梵/游水/MS Wong. Remaining intake friction is **new activity nouns**, not a new parser strategy. Operator chose eight titles from HK 3-year-old activity research (`my-notes/phase-10-hk-parent-activities.md`); not the rest of that catalog.

Grow from that list. Titles belong in the parser so Confirmation / Writer see a real `title` without extra modules.

公園 / 商場 as place words are **not** create signals by themselves (chat about a park stays `unknown`). Class/errand nouns may be create signals.

## Time box

1–2 hours. Green the locked fixtures and stop.

## In Scope

### Parser heuristics only

Keep `parse(message, *, now=, correlation_id=) -> ParseResult`.

1. Title keywords / synonyms → canonical titles in the table above.
2. `游水班` / `swim class` keep title `游泳` (do not invent a second swim title).
3. 公園 / playground is a title, not a create signal alone.
4. Document the new titles in `parser.py` module docstring.

Listener / Confirmation / Writer / overlap pick this up automatically. No code changes there.

## Out of Scope

| Item | Why later |
|------|-----------|
| Writer / Listener / overlap / reader edits | Parser is the title seam |
| Other catalog titles (買餸, 迪士尼, phonics, 科學館, …) | Operator did not pick them |
| Bare-hour AM/PM guessing | Unchanged |
| LLM / hybrid parser | Rules first + ADR only if still painful |
| Mini launchd | Operator; not this PR |
| Freebusy, update/delete, reminders | Later |

## Unit test plan (locked for implementation)

Authoritative Phase 10 test decision. Implements [unit-testing.md](../docs/unit-testing.md).

### Layout

| Path | Role |
|------|------|
| `tests/test_parser.py` | T1–T9 + existing F/L/A/Q stay green |
| Other suites | W5 = full pytest |

### Determinism

`FIXED_NOW = 2026-08-08 12:00 Asia/Hong_Kong` (Saturday noon). No network, no LLM.

### Fixtures / cases

| ID | Phrase | Expect |
|----|--------|--------|
| **T1** | `聽日下午3點去公園` | `create_event`; title 公園; start 2026-08-09 15:00 HKT; `missing_fields` empty |
| **T2** | `Sunday 10am playgroup` | `create_event`; title playgroup; start 2026-08-09 10:00 HKT |
| **T3** | `聽日上午9點游水班` | `create_event`; title 游泳; start 2026-08-09 09:00 HKT |
| **T4** | `星期六下午2點體能班` | `create_event`; title 體能班; start 2026-08-08 14:00 HKT |
| **T5** | `聽日上晝11點手作工作坊` | `create_event`; title 手作; start 2026-08-09 11:00 HKT |
| **T6** | `聽日下午4點去商場` | `create_event`; title 商場; start 2026-08-09 16:00 HKT |
| **T7** | `Sunday 3pm birthday party` | `create_event`; title 生日會; start 2026-08-09 15:00 HKT |
| **T8** | `聽日上午10點打針` | `create_event`; title 打針; start 2026-08-09 10:00 HKT |
| **T9** | `公園啲花好靚。` | `unknown` (公園 is not a schedule signal) |
| **A1** | `聽日9點，梓梵游水` | unchanged create 游泳 + Cedric |
| **W5** | full `pytest` | previous suites green |

English synonyms (playground, 遊戲班, gymnastics, gym, workshop, mall, vaccine) are covered by the same keyword table; T2/T4/T5/T7/T8 lock the English or mixed forms that matter. Extra synonym strings are not separate tests this session.

Named tests:

- `test_parse_ting_yat_park` → T1  
- `test_parse_sunday_playgroup` → T2  
- `test_parse_swim_class` → T3  
- `test_parse_gymnastics_class` → T4  
- `test_parse_workshop` → T5  
- `test_parse_mall` → T6  
- `test_parse_birthday_party` → T7  
- `test_parse_vaccine` → T8  
- `test_parse_park_chat_is_not_create` → T9  

### Non-tests

- Slack / Socket Mode
- Writer / overlap / reader code changes
- Live Google
- Titles beyond the locked table
- LLM
- Operator Mini launchd

### Minimum green bar

9 new tests + full suite green.

## Logging & retention applicability

| Data | Class | Phase 10 action |
|------|-------|-----------------|
| Parser logs | **A** | Existing `parse_started` / `parse_completed`; 14d file purge |
| Parse results | — | Not persisted |
| Secrets | — | Unchanged |

No new store.

## Acceptance Criteria

- [x] This file locked before implementation
- [x] T1–T8 are `create_event` with the canonical titles and starts above
- [x] T9 park chat stays `unknown`
- [x] T3 title is `游泳`, not a new “游水班” title
- [x] F1–F5, L1–L4, A1/A3/A6, list Q1–Q3 unchanged
- [x] Writer / Listener / overlap source **unchanged** this session
- [x] No LLM; default pytest offline
- [x] `uv run pytest` and `ruff check .` clean
- [x] Architecture, README, `PROGRESS.md`, parser docstring updated
- [ ] Live Slack smoke **not** required (stretch: Mini listener + post T1)

## Suggested session order (TDD)

1. Lock this document.  
2. Red tests T1–T9.  
3. Title + create-signal heuristics until green.  
4. Docs; `pytest` + `ruff`.  
5. Stop before extra catalog titles, Writer, or LLM.

## Success definition

Phase 10 is **done** when the eight operator-picked activities parse as confirmable `create_event`s (with day + time), and a park mention without a schedule is still not a calendar create.

## Explicit non-goal

Do not ingest the rest of the HK activity catalog. Do not start an LLM parser.
