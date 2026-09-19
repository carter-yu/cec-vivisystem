# ADR 0006: Retry-safe Calendar IDs and atomic JSON stores

Date: 2026-09-19. Status: Implemented in the local Phase 21 patch.

## Context

Audit-only deduplication cannot prevent a second Calendar insert after a successful
remote insert loses its response, or when the local success audit cannot be saved.
Slack redelivery can also create another confirmation. JSON overwrites can truncate
state; concurrent listener workers can race read/modify/write operations.

## Decision

- Derive Google event IDs from SHA-256 of `cec-confirmation:` plus confirmation ID.
  The hex representation uses characters allowed by Google's event-ID format.
- Keep audit-based short-circuiting. On an insert 409, fetch the event and verify
  its private confirmation property and non-cancelled status before returning it.
- Derive Slack-origin confirmation IDs from channel and source message ID. Reuse
  existing records, including terminal records. Slack notes similarly deduplicate
  their source; standalone notes retain their original creation behavior.
- Serialize intake within one listener process. Operate one listener per store;
  this is not distributed locking or a new orchestrator.
- Replace JSON through a flushed/fsynced sibling temporary file and atomic rename.
  Schemas and retention classes are unchanged. Temporary files are not a new store.

## Consequences and limits

New provider-side identities remain stable across retries and local audit loss.
Existing events with Google-generated IDs still require their old audit records.
The identity is scoped to a target Calendar; a different confirmation remains a
separate explicit request. Do not change the ID derivation casually.

Atomic replacement preserves an old record on a failed replacement, but does not
make Slack post + marker save transactional or guarantee recovery from every power
loss. A killed process may leave an ignored `.tmp` sibling. Multiple listener
processes and overlapping scheduled jobs are not made transactional by this ADR.

The [Google creation guide](https://developers.google.com/workspace/calendar/api/guides/create-events)
documents client-provided IDs for avoiding duplicates after uncertain insert results.
