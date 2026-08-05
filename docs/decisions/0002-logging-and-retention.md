# ADR 0002: Logging Completeness and Data Retention

## Status
Accepted

## Context
cec-vivisystem is a multi-component family swarm. Failures will span Slack, parsing, confirmation, and Google Calendar. A busy parent debugs only in short weekend windows, so each component needs enough structured logs to reconstruct a flow quickly.

At the same time, the system will handle family message text and calendar operations. Unbounded logs and shadow databases are a privacy and disk risk on a home Mac Mini. We need explicit retention and purge expectations before more components grow stores.

## Decision
1. **Structured boundary logging is mandatory** for every component (start/end, outcome, errors, `component`, `event`, and `correlation_id` when a flow exists). Details live in [docs/logging-and-retention.md](../logging-and-retention.md).
2. **Data is classified** into retention classes with fixed periods:
   - Application logs: **14 days**
   - Audit (confirmations + calendar write attempts/results): **90 days**
   - Operational state (pending work): **terminal + 7 days** (cap **30 days**)
   - Dead letters: **30 days**
   - Health detail samples: **14 days**
   - Life notes (user content): **until family deletes** (not treated as logs)
   - Google Calendar remains source of truth; no full local calendar mirror
3. **No persistent store without a purge story** (script acceptable before automation).
4. **Secrets never logged**; raw message bodies only in short-lived app logs, not eternal audit, unless a specific audit field is justified later.
5. Phase 1 (Parser) implements boundary logs only; file rotation and purge tooling wait until durable sinks exist.

## Consequences
- Slightly more logging code per component; faster incident response.
- Implementers must pick a data class when adding disk/state.
- Soft disk caps reduce surprise disk-full failures.
- Retention periods can be revised by a new ADR if real usage shows they are too short/long.
