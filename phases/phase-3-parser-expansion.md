# Phase 3 – Rule Parser Expansion (Live Family Phrases)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

This phase **specializes** the unit-testing and logging standards for a parser *extension*. It does **not** change the Phase 1 `parse` → `ParseResult` contract. Phase 1 fixtures (F1–F5 + clarification/unknown) must remain green.

**Goal**  
Reduce live Slack friction: expand the offline **rule/heuristic parser** so common family Cantonese (and mixed) create-event phrases become `create_event` instead of `needs_clarification`. Same contract, no LLM, no Slack/Calendar changes required for acceptance.

**Why this phase (decision)**  
Phase 2 intake works (Three of Us / `#family-plans` → parse → reply). Real messages often return `needs_clarification` (e.g. 聽日, 上晝, place + class titles). Grow-from-need (§4.4 / §4.4.1): **understand messages before** Confirmation or Calendar. Confirmation on weak parses wastes the weekend; LLM is not the default.

**Time box**  
1–2 hours. Ship green tests + documented weekday/relative policy updates; stop expanding mid-session once locked fixtures pass.

## In Scope

### Component: `Parser` (extension only)

- Keep public API: `parse(message, *, now=, correlation_id=) -> ParseResult`.
- Add **locked live-style fixtures** (below) covering friction seen in real use.
- Extend rules/heuristics only as needed to green those fixtures:
  - Relative day: **聽日** (and keep 明天 / tomorrow)
  - Day periods: **上晝** / **下晝** (map to morning/afternoon clock rules; document)
  - Clock forms already partially supported; ensure `11點` + 上晝 → usable start
  - Title / activity: class / lesson style (e.g. Miss Wong 堂), optional location (e.g. 銅鑼灣)
  - Participants: continue Cedric / Elaine / Carter when present
- Document any new weekday/relative policy in `parser.py` module docstring (one place).
- Boundary logs unchanged in spirit (`parse_started` / `parse_completed`); no new durable store.

### Contract (unchanged)

Phase 1 fields remain the acceptance contract. Phase 3 must not rename or drop them. Downstream Listener keeps calling `parse` as today.

## Out of Scope

| Item | Why later |
|------|-----------|
| Confirmation Guardian / pending store | Phase 3 is understand-first; confirm after parses are useful |
| Calendar Writer / read / freebusy | Write gate still closed (ground rule 6) |
| LLM / hybrid parser | Only if rules still fail after this expansion + ADR |
| Slack Listener behavior changes | Not required; replies improve automatically when parse improves |
| Multi-intent NLU (cancel, query free time, notes) | Still create-event + clarification/unknown |
| Perfect dialect coverage / unbounded phrase list | Time-box to locked fixtures only |

## Unit test plan (locked for implementation)

Authoritative Phase 3 test decision. Implements [unit-testing.md](../docs/unit-testing.md). Do not expand fixtures mid-session unless a contract bug appears.

### Layout

| Path | Role |
|------|------|
| `tests/test_parser.py` | Keep Phase 1 tests; **add** Phase 3 cases (or thin `tests/test_parser_expansion.py` if clearer—prefer one file unless noisy) |
| Phase 1 F1–F5 + B–D | **Must stay green** (regression bar) |

### Determinism rules

Same as Phase 1:

1. `FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))` (Saturday noon HKT) for all relative-date tests.
2. Family TZ `Asia/Hong_Kong`; timezone-aware `start` when time is known.
3. No network, no LLM, no filesystem side effects.
4. Public API: `parse(...)` only.

### Relative anchors (additions)

For `FIXED_NOW` = Sat 2026-08-08 12:00 HKT:

| Phrase | Meaning (Phase 3 policy) | Date |
|--------|--------------------------|------|
| 聽日 | Same as 明天 / tomorrow | 2026-08-09 |
| 上晝 | Morning period; with hour `N點` treat as morning (hour &lt; 12 stays AM unless later policy says otherwise) | — |
| 下晝 | Afternoon period; with hour `N點` treat like 下午 (add 12 if hour &lt; 12) | — |

Document final period→hour mapping in `parser.py` if implementation differs slightly; adjust only the locked fixtures below—not ad-hoc per test.

### Required new fixtures (create_event)

| ID | Input (`message`) | Expected `intent_type` | Must assert |
|----|-------------------|------------------------|-------------|
| L1 | `聽日上晝11點，帶Cedric去銅鑼灣上Miss Wong 堂` | `create_event` | start = 2026-08-09 11:00 HKT; participants include Cedric; title non-empty and relates to Miss Wong / 堂 / lesson/class; location relates to 銅鑼灣 if filled (optional but preferred); `missing_fields` empty; confidence not low |
| L2 | `聽日下午3點帶 Cedric 去游泳` | `create_event` | start = 2026-08-09 15:00 HKT; title relates to 游泳/swim; Cedric in participants (regression-style with 聽日) |
| L3 | `下晝2點 Elaine 睇牙醫` | `create_event` | Needs a date: if only 下晝+time without day, expect **`needs_clarification`** with missing date/start **or** document that 下晝 alone is incomplete — **locked expectation: `needs_clarification`**, `missing_fields` includes date/start-related; must not invent a fake date |
| L4 | `明天上晝10點 pediatrician for Cedric` | `create_event` | Mixed; start = 2026-08-09 10:00 HKT; pediatrician + Cedric |

**Note on L1:** This is the live smoke phrase class that previously returned `needs_clarification` with `missing_fields=['title','start']`. Phase 3 success means L1 greens as `create_event`.

### Failure / regression (named)

| ID | Test intent |
|----|-------------|
| R1 | All Phase 1 F1–F5 still `create_event` with prior assertions |
| R2 | Phase 1 `幫我 book 游泳` still `needs_clarification` (missing time/date) |
| R3 | Phase 1 `今日天氣點呀` still `unknown` |
| L5 | Garbage / emoji-only still does not raise |
| L6 | Log boundary: `parse` on L1 completes with logging configured |

### Named unit tests (implement these)

- `test_parse_create_event_ting_yat_morning_class_causeway` → L1  
- `test_parse_create_event_ting_yat_afternoon_swim` → L2  
- `test_parse_needs_clarification_period_time_without_date` → L3  
- `test_parse_create_event_mixed_tomorrow_morning_pediatrician` → L4  
- Existing Phase 1 tests unchanged (R1–R3 covered by current suite)  
- `test_parse_live_phrase_does_not_raise_on_garbage` → L5 (may reuse Phase 1 garbage test)  
- `test_parse_logs_boundary_live_fixture` → L6 (optional if Phase 1 log test already sufficient; prefer one assert on L1)

### Explicit non-tests (Phase 3)

- Slack payloads / Socket Mode  
- Confirmation / calendar APIs  
- LLM mocks  
- Every Cantonese dialect variant beyond locked fixtures  
- Changing Listener reply templates (optional polish only if time left)  
- New durable stores  

### Minimum green bar (count)

| Category | Min tests |
|----------|-----------|
| New create-event L1, L2, L4 | 3 |
| Needs clarification L3 | 1 |
| Phase 1 suite | still all green (~12 parser) |
| Optional log/garbage | 0–2 if not already covered |
| **New tests** | **~4–6** |

Full suite ≈ 35–37 after Phase 3.

### Implementation order (TDD)

1. Add L1–L4 tests (red).  
2. Extend heuristics (聽日, 上晝/下晝, title/location keywords) until green.  
3. Confirm Phase 1 suite still green; ruff clean.  
4. Update architecture / PROGRESS; optional one live Slack smoke of L1-style phrase.

## Logging & retention applicability

| Data | Class | Phase 3 action |
|------|-------|----------------|
| Parser / listener app logs | **A** | Existing file + stdout path; no new store |
| Parse results | — | Still not persisted |
| Secrets | — | Unchanged (ground rule 13) |

No new retention classes. Startup archive/purge already covers class A files.

## Acceptance Criteria

- [x] Locked fixtures L1, L2, L4 return `create_event` with documented assertions under `FIXED_NOW`
- [x] L3 returns `needs_clarification` without inventing a date
- [x] All Phase 1 parser tests remain green
- [x] `parse` → `ParseResult` contract fields unchanged
- [x] No LLM; no network; default `pytest` offline and secret-free
- [x] `uv run pytest` passes; `uv run ruff check .` clean on touched code
- [x] Module docstring documents 聽日 / 上晝 / 下晝 policy
- [x] Architecture + `PROGRESS.md` updated; README phase link
- [ ] Optional: live smoke L1-style phrase in `#family-plans` shows create-event summary (manual)

## Suggested session order (implementation weekend)

1. Red tests L1–L4.  
2. Minimal rule changes to green.  
3. Regression + ruff.  
4. Docs / PROGRESS; optional live smoke.

## Success definition

Phase 3 is **done** when the live-style phrase class (L1) and the other locked expansions parse to structured create-event (or explicit clarification for L3) offline, with Phase 1 regressions green—without Confirmation, Calendar, or LLM. That makes the existing Listener replies useful for real family speech and unblocks a later Confirmation phase.

## Explicit non-goal

Do not start Confirmation or Calendar “while we’re in the parser anyway.” One friction: live phrase understanding.
