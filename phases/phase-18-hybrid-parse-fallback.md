# Phase 18 – Hybrid parse fallback + miss tracking

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  
- [ADR 0005](../docs/decisions/0005-hybrid-parse-fallback.md)  

Rules stay the default parser. LLM is a **fallback** when a create-looking line is `unknown` / `needs_clarification`. Misses are stored so common tokens can become rules. **No calendar write without yes.**

## Goal

1. Elaine can type a new create-shaped line and still get a **proposal to confirm**, not a dead reject.
2. Every such rule-miss is recorded (raw text + rule intent). A CLI lists repeated tokens for the next parser phase.
3. Default `pytest` never calls SpaceXAI. Fake LLM only.

## Why this phase

Incident 2026-09-14: token ok; four creates failed in rules. Growing rules only from her next failures is a bad family UX. Hybrid fallback is the product fix; miss store is how we still **promote keywords into rules** without using her as the only catalog.

## Time box

1–2 hours. Fake-LLM tests green. Live key is operator.

## In Scope

### 1. `parse()` unchanged (rules)

Existing F/L/A/W fixtures stay green with **no** LLM.

### 2. `parse_with_fallback`

```text
parse_with_fallback(message, *, now=, rule_result? or rule_parse=, llm=, miss_store=) -> ParseResult
```

Call LLM only when:

- rule intent is `unknown` or `needs_clarification`
- `_looks_like_create(message)` (time/date/add/go-with fragments)
- not weather-only, not empty, not help/list/important-date (those never reach fallback)

If `llm` is None: record miss (if store) and return the rule result.

If LLM returns `create_event` with title **and** start: use it (`notes=llm_fallback`, confidence medium). Else keep clarification / unknown.

LLM failure: log `parse_fallback_failed` with model, tokens, `latency_ms`; return rule result.

### 3. `LlmParser` protocol

- `FakeLlmParser` — tests inject a canned `ParseResult` or raise.
- `XaiChatLlmParser` — live; lazy-import `openai`; `XAI_API_KEY`; base `https://api.x.ai/v1`; model `LLM_MODEL` or `grok-4.5`. JSON object only. Timeout 15s. Prompt file versioned under `src/cec_vivisystem/prompts/`.

Allowed LLM intents: `create_event` | `needs_clarification` | `unknown`. Participants restricted to family aliases (Cedric, Coco, Elaine, Carter) that appear in the message (or their surface forms). No tools.

### 4. Miss store

`data/parse_misses/` JSON (class **C**, 90d purge on Listener/CLI start). Fields: `miss_id`, `recorded_at`, `raw_text`, `rule_intent`, `rule_missing`, `llm_intent`, `llm_used`, `tokens`, `correlation_id`. Never tokens/secrets.

`summarize_parse_misses(store, *, min_count=2)` → `(token, count)` descending. Skip known titles/aliases/function words. CLI: `uv run python -c "from cec_vivisystem.parse_misses import main; main()"`.

### 5. Listener

`process_slack_message_event` optional `llm_parser=` + `miss_store=`. Socket Mode: miss store always; live LLM if key present. Confirmation path unchanged. Proposal may note `llm_fallback` so the family checks title/time.

Unknown after fallback: two copy-paste examples + `help`, not only `missing: start`.

### 6. Unit tests (`tests/test_parse_fallback.py`, `tests/test_parse_misses.py`)

No network. Fake LLM.

| ID | Case | Expect |
|----|------|--------|
| H1 | Rules already `create_event` | no LLM call; no miss |
| H2 | Create-looking unknown + Fake returns create | `create_event`; miss recorded; `notes=llm_fallback` |
| H3 | Fake raises | rule result kept; miss recorded; log failed |
| H4 | Weather `今日天氣點呀` | no LLM; no miss |
| H5 | No llm client, create-looking unknown | miss recorded; unknown stays |
| H6 | Contract fields on fallback result | title, start, participants, notes |
| H7 | Telemetry log on Fake success | `parse_fallback_succeeded` + model + `latency_ms` |
| M1 | Two misses with the same novel token | summarize count ≥ 2 |
| M2 | Known title 游泳 not listed as new keyword | skipped or not top |
| L1 | Listener + Fake: unknown-looking create → proposal, no Google write | pending confirmation |

**Non-tests:** live SpaceXAI, Mini key, auto-adding rules from summarize (operator still locks a parser phase).

## Out of Scope

| Item | Why later |
|------|-----------|
| LLM on every message | Cost; rules stay first |
| Auto-writing new regex from misses | Human + phase doc |
| LLM list / important dates / life notes | Create-fallback only |
| Calendar write without yes | Ground rule 6 |

## Logging / retention

`component=parse_fallback` / `parse_misses`. Required LLM fields: `model`, `prompt_tokens`/`completion_tokens` (0 on Fake), `latency_ms`, `outcome`. Never API keys. Miss rows class **C** 90d. App logs class **A**.

## Acceptance

- [x] H1–H7, M1–M2, L1 + prior suite + ruff
- [x] `parse()` tests still never construct a live LLM
- [x] ADR 0005 + `.env.example` `XAI_API_KEY` names only
- [x] PROGRESS / architecture / README

## Done when

A create-looking line that rules miss can become a **yes**-gated proposal via Fake LLM in tests, and the raw line is in the miss store for keyword promotion.
