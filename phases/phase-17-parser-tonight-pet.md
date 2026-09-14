# Phase 17 – Parser 今晚 / 今日 create + pet Coco

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  
- [Phase 1](phase-1-parser.md) · [Phase 9 aliases](phase-9-parser-aliases.md) · [Phase 10 titles](phase-10-parser-titles.md) · [Phase 14 list 今日](phase-14-period-recap.md) · [Phase 15 important dates](phase-15-important-dates.md)  

Parser-only. Same `parse` → `ParseResult` contract. **Does not** change Writer, Listener, overlap, or Reader. **No LLM.**

## Goal

Family `#family-plans` create lines from incident **2026-09-14** become `create_event` (proposal + yes) instead of `unknown` / `needs_clarification`. Canonical family pet participant is **Coco**. Yearly birthday add works via existing important-dates path.

## Why this phase (decision)

`incident-logs/2026-09-14/` (`parser-2026-09-14.log` / `listener-2026-09-14.log`). Calendar token was **ok** (`google_token_ok`, `聽日有乜` listed). Wife still could not add events:

| Phrase | Parsed | Missing |
|--------|--------|---------|
| `今晚10點，同椰子糖洗耳仔` | `needs_clarification` | `title`, `start` |
| `加個Event，今日2:30 ，梓梵物理治療` | `unknown` | — |
| `加活動，今日2:30 ，梓梵物理治療` | `unknown` | — |
| `加活動：今晚10點，同椰子糖洗耳仔` | `needs_clarification` | `title`, `start` |

Causes (rules, not Writer): **今晚** is not a create date (10點 is a clock with no day); **今日** is list-only (Phase 14); bare **`2:30`** after CJK is not a clock (`\\b` fails on `日2`); **加活動** / **加個Event** are not create signals; **洗耳仔** / **物理治療** are not titles; **椰子糖** is not a participant. 梓梵→Cedric already works once the rest parses.

Grow from those four lines. Do not jump to LLM.

## Time box

1–2 hours. Green the locked fixtures and stop.

## In Scope

### Parser heuristics

Keep `parse(message, *, now=, correlation_id=) -> ParseResult`.

1. **今日 / 今天 / today** in `_extract_date` for **create** (same calendar day as `now` HKT). Still not a create **signal** alone (`今日天氣點呀` stays `unknown`).
2. **今晚 / 今夜** → that HKT calendar day. With a clock, hours 1–11 become PM (**今晚10點** → 22:00). Without a clock → `needs_clarification` missing `start` (do not invent 20:00).
3. Bare colon times (`2:30` / `14:30`) match next to CJK (no `\\b`). **今日** + hour 1–7 without am/pm/上晝 → afternoon (**今日2:30** → 14:30). Explicit `am`/`pm` / 上晝/下晝 still win. Bare `N點` unchanged (9點 → 09:00).
4. Create signals: **加活動** / **加個活動** / **加個Event** / **加event** (case-insensitive).
5. Titles: **物理治療** / physio / physiotherapy → `物理治療`; **洗耳仔** / 洗耳 / ear cleaning → `洗耳仔`. These may be create signals.
6. Pet aliases → participant **Coco** (not a create signal alone): **椰子糖**, **糖糖**, **Lady Coco**, **Coco**. Longer forms first. Dedup like 梓梵→Cedric.
7. Important dates: `4月21日 椰子糖生日` → yearly add; participant Coco; no calendar write. Birth year 2021 is **not** stored (yearly, same as 梓梵).
8. `help` / `指令` lists 今晚, 今日 create, 加活動, 洗耳仔, 物理治療, Coco / 椰子糖, `4月21日 椰子糖生日`.

Listener / Confirmation / Writer pick this up automatically.

## Out of Scope

| Item | Why later |
|------|-----------|
| Writer / Listener / overlap / reader edits | Parser is the seam |
| 聽晚 / 今朝 / 今晚 without clock inventing an hour | Do not guess |
| Bare `N點` AM/PM guessing | Unchanged 9點 → 09:00 |
| Seeding `data/important_dates/` in git | gitignored; family posts `4月21日 椰子糖生日` after Mini pull |
| LLM parser | Rules first; ADR only if still painful |
| Update/delete calendar events | Later |

## Unit test plan (`tests/test_parser.py`)

`PHASE17_NOW = 2026-09-14 09:51 Asia/Hong_Kong` (live log local time). No network, no LLM.

| ID | Message | Expect |
|----|---------|--------|
| W1 | `今晚10點，同椰子糖洗耳仔` | `create_event`; title 洗耳仔; start 2026-09-14 22:00 HKT; **Coco** |
| W2 | `加個Event，今日2:30 ，梓梵物理治療` | `create_event`; title 物理治療; start 2026-09-14 14:30 HKT; **Cedric** |
| W3 | `加活動，今日2:30 ，梓梵物理治療` | same as W2 |
| W4 | `加活動：今晚10點，同椰子糖洗耳仔` | same as W1 |
| W5 | `4月21日 椰子糖生日` | `add_important_date`; yearly; Coco; not create |
| W6 | `椰子糖好得意` | `unknown` (pet name is not a create signal) |
| W7 | `今日天氣點呀` | still `unknown` |
| W8 | `聽日9點，梓梵游水` | unchanged create; Cedric; 09:00 |
| W9 | `今晚洗耳仔` (no clock) | `needs_clarification`; missing `start` |
| H | `help` text | contains 今晚, 加活動, 椰子糖 or Coco, 4月21日 |

**Non-tests:** live Slack, Google, launchd, 聽晚, LLM.

## Logging / retention

Class **A** only (`parse_started` / `parse_completed`). No new store. No calendar write.

## Acceptance

- [x] W1–W9 + H green; prior suite + ruff
- [x] Module docstring + help list updated
- [x] PROGRESS / architecture / continue-note
- [ ] Mini pull still operator (live Slack sees this after kickstart)

## Done when

Those four 2026-09-14 phrases parse as creates (or important-date add for the birthday). Family still confirms with thread **yes**. 梓梵 remains Cedric. Coco is the cat.
