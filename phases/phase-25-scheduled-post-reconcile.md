# Phase 25: Scheduled-post reconciliation

Date: 2026-09-28. Scope: one offline slice, one PR, no deployment.
Inherits the ground rules, unit-testing standard and logging/retention standard.

## Policy and scope

Morning recap, token reminders and important-date reviews persist an attempt
before calling Slack. An unresolved attempt requires manual reconciliation;
automatic runs never retry it. Successful records retain Slack channel and ts.
Existing successful markers remain valid without a handle. See ADR 0009.
No Calendar, parser, listener, LLM, scheduling or class-F content changes.
Time box: one focused implementation session.

## Locked regression plan (before implementation)

Use fixed HKT clocks, temporary JSON directories and fake Slack/Google only.

| Case | Expected evidence |
| --- | --- |
| All three scheduled paths succeed | Handle persists after reopening; repeat skips |
| Slack accepts then loses response | Failure, unresolved marker, repeat makes no post |
| Crash after reservation, before Slack | Unresolved marker blocks restart |
| Crash after Slack response, before final marker | Restart blocks duplicate |
| Reservation write fails | No Slack call; failure |
| Final marker save fails | Failure, unresolved marker retained; no automatic repost |
| Multi-occurrence partial reservation/finalization | No duplicate batch or partial repost |
| Legacy marker | Skip without requiring a new Slack handle |
| Invalid response or damaged marker | Fail closed; no automatic retry |
| Adapter contract | Fake SDK returns channel/ts; SDK retries disabled |
| Retention and logs | Existing class-C cutoff; failure/reconciliation boundary visible |

Non-tests: live Slack/Google/models, Mini, launchd, credentials, billing, concurrent
processes, power-loss durability, automated Slack history lookup or message edits.
Acceptance: full pytest, Ruff, locked dependency sync, diff check; prompt archive
and factual PROGRESS entry; reviewable PR without merge.
