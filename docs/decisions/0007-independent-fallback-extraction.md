# ADR 0007: Extract independently after a rule miss

## Status
Accepted — 2026-09-23, within ADR 0005's create-only fallback.

## Context
The incident provider exports returned valid clarification JSON despite supplied
activities and times. They repeated the rule parser's missing-field lists. The
rules use a finite title vocabulary: missing from that vocabulary does not mean
missing from the user's message. Prompt anchoring is a plausible explanation,
not proven model-internal behavior. A separate reproducible regex defect missed
AM/PM clocks adjacent to Chinese text.

## Decision
Keep rule results for eligibility and diagnostics, but omit their intent and
missing-field verdicts from the live model request. Send only the original message
and reference time in JSON. Version the revised prompt as v3 and preserve v2 for
comparison. Explicitly permit unfamiliar and compound activity descriptions,
define weekday and AM/PM handling, and provide positive and incomplete examples.
Do not infer missing duration, participant or location requirements.

Keep validation, allowed intents/participants, exception-only model failover and
explicit confirmation unchanged. A legitimate clarification must not trigger a
second model merely to obtain an affirmative answer. Fix CJK clock adjacency in
rules without adding private incident activities to the title dictionary.

## Consequences and verification
Prompt version/hash and remaining missing fields accompany fallback completion
logs; clarification is a partial outcome even when the API call succeeded.
No new persistence, provider, dependency or model selection policy is introduced.
Synthetic offline tests verify clock parsing, request shape, response validation,
and pending confirmation without writes. They do not measure live prompt quality.
Live evaluation must record model and prompt hash and include complete, compound,
missing-date/time/title and non-create cases before claiming extraction accuracy.
