# Phase 22 — Single-owner Socket Mode recovery

## Goal and scope

Fix the listener's competing forced reconnect path observed in the September 21
incident. The application health loop calls `connect_to_new_endpoint(force=True)`
while the Slack SDK also reconnects. In locked slack-sdk 3.43.0, force bypasses
the connected-state condition and can proceed even if lock acquisition failed.
The logs demonstrate repeated reconnect/error cycles and very little intake;
they do not prove what caused the original transport failure.

Slack SDK owns connection recovery. Keep the application check read-only, retain
visible disconnected/status-error reporting, and report observed recovery without
claiming successful reconnection merely because an API returned. Preserve SDK
auto-reconnect, ping settings, intake serialization, and the Calendar gate.

Inherits ground rules, unit-testing and logging/retention standards. One focused
maintenance slice. No provider/dependency change, parser change, new watchdog,
deployment, scheduled posts, credential changes, or production message replay.

## Locked regression plan (before implementation)

| Case | Expected |
| --- | --- |
| Connected socket | `ok`, no reconnect call |
| Disconnected socket over repeated checks | `disconnected`, warning, no endpoint replacement |
| Status inspection raises | `failed`, structured error, no reconnect |
| SDK recovers between observations | subsequent check returns `ok`; application has not replaced the session |
| Real listener wiring with fake Bolt handler and temporary stores | auto-reconnect remains enabled; disconnect then recovery is observed; one initial connect; cleanup on interrupt |
| Synthetic create delivered after recovery | proposal and pending confirmation, no Calendar write until thread yes; repeated yes remains deduplicated (existing suite) |

Use fake clients/handler, fixed clock, temporary stores, and no real sleeps or
network. Update the obsolete forced-reconnect expectations in listener tests.
Run focused regressions before the implementation and the full suite afterward.

## Logging and retention

Class A only, existing 14-day retention. `socket_mode_disconnected` reports that
SDK recovery owns the connection; `socket_mode_status_failed` reports inspection
errors; `socket_mode_recovered` records a later observed connected state. These
are transport observations, not proof of Slack message delivery. No new store.

## Acceptance

- No application-initiated endpoint reconnects during monitoring.
- Fake runtime wiring proves SDK auto-reconnect and cleanup remain enabled.
- Tests and Ruff pass; diff reviewed; PROGRESS updated.
- Mini rollout remains a separate operator step: update the existing checkout,
  sync locked dependencies and restart only the listener. Do not start a second
  listener or replay old messages. Verify help/list and a synthetic create proposal,
  explicit yes and repeated yes; observe recovery after a controlled reconnect.
- If SDK-only recovery still fails on Mini, collect fresh transport evidence;
  do not reintroduce a parallel forced reconnect loop.

## Verification

Before the implementation, the focused suite produced four failures and one pass:
the old health check replaced sessions and claimed reconnection on status errors.
After the change: 282 tests pass, Ruff passes, and `git diff --check` passes.
Three obsolete tests were updated and one runtime integration test was added.
Runtime integration uses fixed time and fake Slack/Google clients; no messages
were sent, no live Calendar write occurred, and no deployment was performed.

SDK behavior was checked against the installed locked 3.43.0 source and the
[official client reference](https://docs.slack.dev/tools/python-slack-sdk/reference/socket_mode/builtin/client.html).
Provider auto-recovery remains responsible for transport failures; this patch
removes our interference and does not claim to eliminate every network failure.
