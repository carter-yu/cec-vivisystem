# Phase 20 – Incident 2026-09-17 (返學 title + LLM timeout failover)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  
- [Phase 18 fallback](phase-18-hybrid-parse-fallback.md) · [ADR 0005](../docs/decisions/0005-hybrid-parse-fallback.md) · [Phase 19](phase-19-incident-2026-09-14-v2.md)  

Parser title + live LLM client. **Does not** change Writer gate, confirmation yes/no, or `parse()` (rules stay LLM-free). **No new LLM intents.**

## Goal

Family `#family-plans` lines from incident **2026-09-17** (`incident-logs/2026-09-17/`):

1. School-drop creates with **返學** become `create_event` in **rules** (proposal + yes; no LLM wait).
2. When a create-looking line still misses rules, SpaceXAI **fails over** to a second model after the primary times out or errors — instead of hanging ~45s and returning clarification.
3. The 15s timeout is real (OpenAI SDK must not retry the same model).

## Why this phase (decision)

`incident-logs/2026-09-17/`. Token ok. Phase 19 phrases (list, 號 important date, 下晝 create) worked. New friction:

| Phrase (synthetic in tests) | Parsed | What the family wanted |
|-----------------------------|--------|------------------------|
| 聽朝 + colon 8:45 + 返學 | `needs_clarification` missing `title` | `create_event` tomorrow 08:45 |
| 加活動 + 聽朝 8:45 + 返學 | same | same |
| 聽日上晝 8:45 + 返學 | same | same |

Then Listener called Phase 18 fallback (`grok-4.5`). Three `parse_fallback_failed` `APITimeoutError` at **46–48s**; `llm_used=False`; Slack still asked for a title.

xAI console (`request-logs-2026-09-16.jsonl`) for the third line (`聽日上晝8:45 帶梓梵返學`):

- Request reached xAI only on the last SDK retry (`15:17:17Z`; local attempt started `15:16:45Z`).
- grok-4.5 **completed** at `15:17:32Z` — 99ms after our client timed out.
- Usage: **753 reasoning tokens**, **3 completion tokens**, assistant `content` **empty**. `HISTORY_STATUS_COMPLETE`.
- grok-4.5 reasoning **cannot be disabled** (default effort `high`). Even a 200 would have failed `json.loads`.

Causes:

- **返學** is not in the title table. Date/time already parsed (`聽朝` / `聽日` + `8:45` / `上晝`).
- `XaiChatLlmParser` sets `timeout=15` but the OpenAI SDK default **`max_retries=2`**. Three attempts × 15s ≈ **45s**. Phase 18 contracted one 15s try.
- No second model. Primary timeout returned the rule miss. Family saw a slow fail, not a failover proposal.
- Leading with grok-4.5 on a 15s Slack budget cannot work: it thinks, then returns no JSON.

Grow 返學 into rules (ADR 0005 miss promotion). Fix the live client: **zero SDK retries**, **non-reasoning SKU first**, grok-4.5 only as the other model (`reasoning_effort=low`), empty assistant JSON is a failure that triggers failover. Do not rebuild Phase 18. Do not send list / important-date through the LLM.

## Time box

1–2 hours. Green the locked fixtures and stop.

## In Scope

### 1. Parser

Keep `parse(message, *, now=, correlation_id=) -> ParseResult`.

`PHASE20_NOW` for this incident: `2026-09-16 12:00 Asia/Hong_Kong`.

1. Title keyword **返學** (Traditional only) → canonical title `返學`. Not a create signal alone (`返學` chatter stays `unknown`).
2. `help` title list includes 返學. One create example: `聽朝8:45帶Cedric返學`.
3. Existing 聽朝 / 上晝 / colon-time / 加活動 / 梓梵→Cedric stay as they are.

### 2. Live LLM client (Phase 18 contract, not a rebuild)

`XaiChatLlmParser`:

- `timeout_s=15` **and** `max_retries=0` (one HTTP attempt per model).
- Empty assistant `content` raises (`empty LLM JSON`) so failover runs; do not treat HTTP 200 + empty body as success.
- `parse_with_fallback(..., llm=, llm_fallback=)`: if `llm` raises, log `parse_fallback_failed` then **one** `parse_fallback_failover` and try `llm_fallback`. If that raises too, keep the rule result (today’s H3 behaviour).
- `load_live_llm_parsers`: Mini still has `LLM_MODEL=grok-4.5`. **Order:** non-reasoning SKU first (`LLM_FALLBACK_MODEL`, default **`grok-4.20-0309-non-reasoning`**), grok-4.5 second. If `LLM_MODEL` is already non-reasoning, it stays first. Same key / base URL. If both names match, no second parser.
- grok-4.5 / grok-4.6 calls set `reasoning_effort=low` (cannot turn reasoning off). Non-reasoning SKU must not send that param.
- Fake LLM in pytest. No network.

Operator may override `LLM_FALLBACK_MODEL`. Set it equal to `LLM_MODEL` to disable failover.

### 3. Tests

| Path | Role |
|------|------|
| `tests/test_parser.py` | S1–S5 |
| `tests/test_parse_fallback.py` | H8–H11 |
| `tests/test_listener.py` | L-school |

| ID | Message / case | Expect |
|----|----------------|--------|
| **S1** | `聽朝8:45帶Cedric返學` | `create_event`; 2026-09-17 08:45; title 返學; Cedric |
| **S2** | `加活動，聽朝8:45，帶Cedric返學` | same |
| **S3** | `聽日上晝8:45 帶Cedric返學` | same |
| **S4** | `返學` | still `unknown` (not a create signal) |
| **S5** | `今日天氣點呀` | still `unknown` |
| **H8** | create-looking miss; Fake primary raises; Fake fallback returns create | `create_event`; fallback called; log `parse_fallback_failover` |
| **H9** | both Fakes raise | rule result; two `parse_fallback_failed`; no create |
| **H10** | rules already create; both Fakes present | no LLM call (H1 still true with fallback wired) |
| **H11** | `load_live_llm_parsers` env + `live_openai_kwargs` | with `LLM_MODEL=grok-4.5`, **first** is non-reasoning SKU, second grok-4.5; `max_retries=0`; no network |
| **H12** | empty / missing assistant content | raises `empty LLM JSON` |
| **H13** | `live_completion_extra` | grok-4.5 → `reasoning_effort=low`; non-reasoning SKU → no extra |
| **L-school** | S1 + fake client | Slack proposal; pending confirmation; no Google write; Fake LLM **not** called |

Named tests:

- `test_parse_ting_chiu_colon_school_run` → S1  
- `test_parse_add_activity_ting_chiu_school_run` → S2  
- `test_parse_ting_yat_morning_colon_school_run` → S3  
- `test_school_run_alone_is_unknown` → S4  
- `test_today_weather_still_unknown_phase20` → S5 (or reuse W7)  
- `test_fallback_uses_second_model_after_primary_failure` → H8  
- `test_both_llm_models_fail_keeps_rule_result` → H9  
- `test_rules_create_does_not_call_fallback_llm` → H10  
- `test_live_llm_parsers_honor_timeout_retries_and_fallback_model` → H11  
- `test_empty_llm_json_is_a_failure` → H12  
- `test_reasoning_effort_only_on_flagship_models` → H13  
- `test_school_run_create_proposes_without_llm` → L-school  

### Non-tests

- Live SpaceXAI / Mini key
- Changing Writer / overlap / important dates
- Teaching LLM `add_important_date` or `list_events`
- Simplified **返学** (rejected; not an alias)
- Auto-adding more school titles (上學, 放學) unless they appear in a later miss summary

## Out of Scope

| Item | Why |
|------|-----|
| Rebuild Phase 18 | extend the live client only |
| New LLM intents | ADR 0005 |
| Calendar write without yes | Ground rule 6 |
| Switching Mini `LLM_MODEL` in `.env` | operator; code default fallback is enough |

## Logging

- Existing `parse_fallback_attempt` / `_succeeded` / `_failed` (once **per model**)
- New: `parse_fallback_failover` WARNING (`component=parse_fallback`, from_model, to_model, `correlation_id`; no tokens)
- Class **A** only. No new store.

## Acceptance Criteria

- [x] This file locked before implementation
- [x] S1–S5 parse
- [x] H8–H13 failover / retries / empty JSON
- [x] L-school
- [x] Prior suites green (W1–W9, V1–V10, H1–H7)
- [x] Writer gate unchanged
- [x] pytest + ruff clean
- [x] architecture, README, PROGRESS, logging matrix, ADR 0005, `.env.example` updated

## Success definition

In `#family-plans`, `聽朝8:45帶Cedric返學` proposes immediately (rules). A still-novel create that rules miss does not sit on grok-4.5 for ~45s: one 15s try, then the fallback model, then clarification only if both fail.

## Explicit non-goal

Do not put the LLM on known phrases. Do not add a retry loop on the same model.
