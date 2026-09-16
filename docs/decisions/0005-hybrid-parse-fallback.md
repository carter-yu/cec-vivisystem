# ADR 0005: Hybrid parse fallback (rules first, LLM second)

## Status
Accepted

## Context
Rule parser expansions (Phases 3, 9–11, 17) only learn after a live Slack miss. That made Elaine the test suite: `unknown` / `needs_clarification` with no proposal. Architecture §4.4.1 already allows an LLM **behind** `parse` → `ParseResult` if friction is sustained. Ground rule 6 still requires human confirmation before any calendar write.

## Decision
1. Keep `parse()` **rules-only** (offline, deterministic, default pytest).
2. Listener (and CLI) may call `parse_with_fallback`: if the rule result is `unknown` or `needs_clarification` **and** the text looks like a create, an injectable LLM fills a `ParseResult`. Successful `create_event` still goes through Confirmation **yes**.
3. Default pytest uses `FakeLlmParser` (no network, no `XAI_API_KEY`). Live Mini uses SpaceXAI (`XAI_API_KEY`, `https://api.x.ai/v1`). One HTTP attempt per model (`timeout=15s`, `max_retries=0`). When `LLM_MODEL` is a always-reasoning flagship (`grok-4.5` / `grok-4.6`), the first call is `LLM_FALLBACK_MODEL` (default `grok-4.20-0309-non-reasoning`); the flagship is the second try with `reasoning_effort=low`. Empty assistant JSON is a failure. Same model is not tried twice.
4. Persist every rule-miss that looked like a create under gitignored `data/parse_misses/` (class **C**, 90 days) so operators can promote repeated tokens into rules (`summarize_parse_misses`).
5. LLM may only return `create_event`, `needs_clarification`, or `unknown`. No tools. No calendar write. No list/important-date hijack.

## Consequences
- Family can confirm a proposal on a new phrase without waiting for a parser phase.
- Repeated miss tokens become the next **rule** weekend (not more LLM).
- Live fallback needs an API key on Mini; without a key, misses are still recorded and Slack still clarifies.
- grok-4.5 can exceed 15s and return **empty** JSON after hundreds of reasoning tokens (incident 2026-09-17 xAI console: 753 reasoning, 3 completion, empty content; client timed out 99ms before xAI marked complete). Failover is one other model, not a retry storm. The non-reasoning SKU goes first.
- A later ADR may change provider/model; the `LlmParser` protocol stays.
