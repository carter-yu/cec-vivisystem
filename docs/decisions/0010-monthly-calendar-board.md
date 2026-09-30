# ADR 0010: Daily month-board PNG alongside today text

Date: 2026-10-01. Status: Accepted for Phase 26.

Use Pillow to render the Sunday-first full-month board specified in
[Phase 26](../../phases/phase-26-monthly-calendar-board.md). The grid model is
immutable; rendering returns PNG bytes without storing event bodies. Font loading
is the only renderer filesystem access. Resolve an injected font, then the bundled
OFL Noto Sans TC asset, then common system fonts; fail visibly when none loads.
The supplied subset lacks the continuation arrow, so render that mark as strokes.

Keep today's text first, then independently read the month, render and upload
using Slack SDK `files_upload_v2` with transport retries disabled. Extend the
existing reservation helper with optional filename/content, retaining ADR 0009's
pending-before-external-call and fail-closed reconciliation policy. Upload completion
returns a file ID: store that ID in `SlackPostReceipt.ts` / `slack_ts` as a documented
surrogate, alongside the destination channel. It is not a message timestamp.

Text markers retain their paths. Board markers use
`data/morning_recap/monthly_board/YYYY-MM-DD.json`: daily artifact keys keep the
month view fresh each morning and suppress same-day duplicates. Both are class C,
with the existing 30-day retention run separately by the same CLI. No PNG or event
body is persisted locally. Logs remain class A; Slack holds the uploaded artifact.

Text and board results remain independent. The CLI exits nonzero if either fails,
without changing a successful text result. List/font/render failures before reservation
can retry the image alone. Upload exceptions or final-save failures retain pending
markers and need manual reconciliation under [ADR 0009](0009-scheduled-post-reconciliation.md).
For files, inspect the retained file ID and destination, not a message timestamp.
No automatic retry, Slack-history lookup, cross-process lock, or exactly-once claim.
