# Phase 24 — LLM-first event creation

Inherits ground rules, unit testing, logging/retention and confirmation contracts.
User authorized replacing rules-first event interpretation on 2026-09-24.
Preserve the uncommitted Phase 23 fixes; no deployment or live provider calls.

## Locked plan (before implementation)

- With a configured model, route deterministic help/list/important-date and input
  rejection first, without running create date/time/title rules. All remaining
  messages go to the model, including novel phrasing without rule keywords.
- Exact thread confirmations and raw-note capture remain in listener dispatch.
- Without a model, retain the existing offline rules as a degraded mode. Do not
  silently switch to those rules after a configured model fails or declines.
- Keep bounded exception-only failover. Exhausted calls produce an explicit
  service-unavailable reply, not a request to supply supposedly missing details.
- Use a concise versioned prompt and provider JSON schema; independently validate
  output types/times in code. Keep source-derived identities and the write gate.
- Legacy parse()/parse_with_fallback() contracts remain for offline callers;
  listener opts into LLM-first. Retire ongoing create vocabulary expansion.

| Regression | Expected |
| --- | --- |
| Known create + conflicting rule title | Model called; full model title preserved |
| Novel wording with no rule signals | Model called without create-rule invocation |
| Help/list/important-date/garbage/script rejection | No model call; existing result |
| No configured model | Existing offline behavior |
| Model unknown/clarification | Never overridden by a rules create |
| Timeout/malformed output and optional failover | Bounded calls; explicit unavailable if exhausted |
| Request contract | JSON schema, compact prompt, no prior parser verdict |
| Bad fields/reversed end/empty title | Never a usable create |
| Listener create/yes/redelivery/raw notes | Existing confirmation, duration and identity contracts |
| Telemetry | Mode, prompt hash/version, latency/tokens; no new raw logging |

Target at least 20 focused cases plus full offline suite, Ruff and diff checks.
Create a synthetic evaluation corpus for later live comparison; fixture/fake
results are not measurements of model quality. No live cost/latency/accuracy
claim, no credential access. Class-A logs unchanged; do not store every LLM-first
message as a rule miss. No new runtime stores or retention classes.


## Local verification result

332 offline tests passed, Ruff and diff checks clean. Active prompt: 238 words;
provider schema also contributes input tokens. Synthetic rules baseline: 7/16
exact expected-field cases. Live model evaluation and deployment remain unrun;
no claim of production accuracy, latency or realized cost savings.
