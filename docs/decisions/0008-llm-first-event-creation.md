# ADR 0008: LLM-first event creation

## Status
Accepted — 2026-09-24, explicitly authorized by the user.
Supersedes ADR 0005's rules-first policy for the configured listener. ADR 0007's
independent extraction and deterministic validation still apply.

## Decision

The objective is reliable natural-language scheduling without continuously adding
activity vocabulary to code. With a model configured, the listener handles existing
deterministic control routes first, then sends remaining text directly to the
create extractor. Create title/date/time rules are not invoked on this path.

Help, supported list and important-date requests, raw-note capture and thread
confirmation remain deterministic. Unsupported phrasing outside those routes may
reach the model, which may only propose creates, clarify, or return unknown; it
cannot perform list/date operations or Calendar writes. Expanding natural-language
coverage for those other intents is a separate slice.

Keep parse() and the legacy fallback default compatible. Without credentials the
listener retains offline rules, reported as offline_rules at startup. After a
configured model fails, use the existing one-other-model exception failover;
if exhausted, explicitly report service unavailability. Never override a valid
model clarification/unknown with a rule create or retry to obtain agreement.
No ongoing create-keyword expansion is expected.

Use concise create_event.v4.txt plus a JSON schema and local validation. The schema
constrains structure, not truth. Preserve one-hour defaults, HKT timestamps, known
participant aliases, confirmation, idempotent writer and injected clients. Keep
existing per-model timeout and zero SDK retries; add a 1,024 completion-token cap.
No whole-interaction deadline is claimed.

Do not store every LLM-first input in the historical rule-miss store. Existing
miss rows/retention and legacy offline diagnostics remain. Successful model
responses record tokens, latency and prompt hash/version under existing class-A
logs. No new stores or dependency are introduced.

## Trade-offs and evidence

All eligible natural-language messages now incur model latency/cost and provider
exposure, including chatter not recognized by the control routes. Provider outages
make model-backed creation unavailable; known phrases no longer silently bypass
that outage. Offline mode remains vocabulary-limited. Model hallucinations remain
possible, so explicit review/confirmation and local validation are essential.

The 16-case synthetic corpus deliberately targets known gaps; its rules baseline
is not a representative production accuracy measurement. Live model quality and
cost must be measured separately. Fake responses demonstrate integration only.

The provider JSON-schema request format was checked against the official
[xAI structured-output documentation](https://docs.x.ai/developers/model-capabilities/text/structured-outputs).
Prompt shortening alone does not prove a token reduction: the schema also consumes
input tokens. Use actual provider usage from the evaluation runner.
