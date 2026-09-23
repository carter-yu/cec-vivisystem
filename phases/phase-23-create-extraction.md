# Phase 23 — Independent create extraction

Inherits the ground rules, unit testing and logging/retention standards, and
ADR 0005. Scope: September 23 parsing incident; no deployment or live writes.

## Evidence and scope

The supplied application logs show six successful model calls: five clarification
results and one create that subsequently reached a confirmed successful write.
Four supplied provider exports return clarification JSON, not transport errors.
Three match application requests; the later fourth request has no corresponding
entry in the supplied application log. The rule parser only recognizes listed
activities. Its Unicode word boundary also misses AM/PM times adjacent to Chinese.
Provider responses repeat the supplied rule missing-fields verdict. Anchoring is
a plausible contributor, not a verified account of model internals.

Remove the rule verdict from the model request; use a versioned independent
extraction prompt with complete JSON examples, weekday/AM-PM semantics, compound
activity preservation, and genuinely missing-detail counterexamples. Fix the
deterministic clock boundary. Keep the rules-only API, validation, failover policy,
participants, default duration and confirmation gate. Do not expand the activity
dictionary merely to memorize the incident.

## Locked regression plan (before implementation)

| Case | Expected evidence |
| --- | --- |
| Chinese weekday directly followed by AM/PM, colon variants, Chinese following suffix | Correct HKT start, including noon/midnight |
| Invalid 0/13 AM-PM and embedded Latin/digit clock fragments | No create from malformed clocks |
| Mock provider request for novel compound activity | Original message and now sent as JSON; no rule verdict; v3 prompt selected |
| Scripted complete extraction | Preserved compound title/start/participant, no missing fields |
| Scripted missing date/time or title; malformed response | Clarification or existing error/failover behavior, never fabricated create |
| Fallback listener integration | Pending proposal, default one-hour end, no Calendar write before yes |
| Boundary logging | Clarification distinguished as partial; create success unchanged |
| Existing suite | Non-create routing, validation, retries and confirmation tests stay green |

All inputs are synthetic, with fixed clocks and fake clients. Prompt tests and
scripted completions verify wiring/contracts, not live model extraction quality.
Live model evaluation and Mini rollout remain unverified by this offline slice.

## Logging and acceptance

No new store or retention class. Class-A application logs retain existing policy;
class-C miss recording stays unchanged. Record prompt version/hash without raw
prompt/completion bodies. Full pytest, Ruff and diff checks must pass; update
PROGRESS with actual results and limits.
