# ADR 0009: Reserve scheduled deliveries before posting

Date: 2026-09-28. Status: Accepted for Phase 25.

## Decision

Favor duplicate avoidance over automatic recovery of a possibly missed message.
Each existing daily/occurrence marker can be `pending` or `posted`. Write all
pending markers before a batch's single Slack call. Each includes an attempt ID,
HKT attempt time, destination, and the complete batch keys. Save returned Slack
channel and timestamp on every finalized marker. Do not store message bodies.
Legacy markers without status remain successful records.

A pending, unreadable or invalid marker fails closed. Posting exceptions, missing
response handles and final-save errors return FAILED, not POSTED. No automatic
retry, including Slack WebClient transport retries. A hard process exit leaves
the pending marker for the next invocation to reject. This does not assert that
Slack accepted a failed call. A reservation failure can leave a partial batch:
those reserved occurrences remain blocked even if Slack was never called.

## Manual reconciliation

Stop overlapping scheduled invocations. Inspect the marker's attempt time,
destination and batch keys privately, then inspect Slack. A saved channel/ts
identifies a known response; pending without a handle is ambiguous, not proof of
absence. For a delivered batch, finalize every pending occurrence using the same
verified handle and original metadata through the store's `write_delivery` API.
Keep `status=posted`, `posted_at`, `slack_channel_id`, and `slack_ts`. Never
blindly delete pending markers or rerun because an error was returned. If absence
is established and a retry is explicitly chosen, the operator may remove only
the affected operational markers using their existing `delete` API before a
manual run. Inconclusive evidence means leave blocked. No reconciliation CLI or
Slack history API is introduced in this slice.

## Compatibility and limits

The same filenames, deduplication keys and 30-day class-C retention remain. Old
markers need no migration. Retention applies to pending and posted markers using
the existing date/occurrence cutoff; reconcile within that window. Historical
replays after retention or lost local state are not protected. Class-F dates and
notes are untouched. New daily keys and yearly occurrences remain independent.

One scheduled invocation at a time remains required. Atomic replacement is not a
cross-process lock, cross-service transaction, backup, or power-loss guarantee.
A crash before Slack may suppress a message; a lost response cannot provide a
handle. A partially finalized batch may retain the handle only in completed
members. The shared attempt ID and batch keys support manual matching. This is
application-level duplicate prevention under preserved local state, not an
exactly-once delivery claim. Logs remain class A; operational markers class C.
