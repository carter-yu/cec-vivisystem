---
date: 2026-10-29 HK
branch: fix/phase-33a-time-root-cause
model: claude-opus-5 (Claude Code)
goal: Find and fix the real cause of a Phase 33a list answering 「有日期但冇時間」 for five lines that each wrote HH:MM, independently of the earlier hypothesis.
constraints: no live xAI, Google or Slack calls (no xAI key on the review host); no Mac Mini, .env or Slack configuration change; no push, merge or PR; local commit only; runtime Chinese stays Traditional; Calendar yes gate, update/delete behaviour and the v5 rollback stay as they are; tests use realistic Slack text (full-width brackets, no-break space, full-width digits/colon) with mocked provider replies; finish with uv run pytest -q and uv run ruff check . green.
acceptance: root cause(s) with file:line evidence and reproduction output; a verdict on dfde7fa; a correct timed v6 reply for the live five-line message gives five included items; diagnostics that make the next live failure readable without logging message content; this archive and a PROGRESS entry.
---

# Manager request (sanitized archive)

A Phase 33a list create failed twice in `#family-plans` on 2026-10-29 (13:00 on
`f07a73d`, 21:09 on `3f9033a` after PR #11, prompt v6 default). One message listed
five dated lines; every line wrote a 24-hour clock. Both times the bot answered
that all five lines had a date but no time, and made no proposal.

Mini log evidence for the second attempt (no raw provider reply is logged):

- `message_received message_length=111`
- `parse_fallback_attempt model='grok-4.20-0309-non-reasoning' rule_intent='unknown'
  parse_mode='llm_first'`
- `batch_extract_completed item_count=5 included_count=0 not_included_count=5
  not_included_reasons='missing_time:5' dated_line_count=5`
- `parse_fallback_succeeded outcome='partial' prompt_version='create_event.v6.txt'
  intent_type='needs_clarification' missing_fields=['time'] prompt_tokens=1347
  completion_tokens=420 latency_ms≈4400`

One minute after the first failure the family answered in the same thread with the
five clocks only and got the generic "Need more detail … (missing: details)" reply.

Earlier attempts: PR #11 (`3f9033a`) read a dropped clock from the item's own line
when `start` was null and the item was not all-day; `dfde7fa` extended that to an
all-day model answer and normalised full-width / no-break text and added a
counts-only log. Neither was confirmed against the live reply.

Requested work:

1. Trace Slack event → routing → `parse_fallback` (prompt, schema, request,
   validation, `_v5_item`, payload mappers) → `event_batch` → clarify copy, and find
   the cause independently. Look for a deterministic code path that reports
   "missing time" for every item even when the model returns correct timed starts.
2. Write offline reproductions with mocked provider replies: correct timed reply,
   start null, all-day, date-only start string, start without an offset,
   `missing_fields ["time"]` with `start` set, and `needs_clarification` with full
   items.
3. Review `create_event.v6.txt`, `CREATE_SCHEMA_V6` and `EVENT_ITEM_SCHEMA` for
   wording and shape that invite a dropped clock.
4. Judge whether `dfde7fa` is correct and sufficient or a band-aid, and propose the
   proper fix (prompt, schema, mapping, deterministic recovery, diagnostics).
5. Review the thread-reply gap and the misleading clarify copy; implement only a
   small and safe change, otherwise improve the copy (Traditional Chinese,
   Cantonese-friendly, plus ASD-STE100 English).
6. Implement the recommended fix on `fix/phase-33a-time-root-cause`.
7. Add diagnostics that make the next live failure self-explaining without logging
   message content.
8. Archive this brief and update PROGRESS.

Redactions in this archive: the live message's therapy activity names and the
family's thread wording (the repository fixture now uses synthetic titles of the
same length, so the 111-character shape of the live message is preserved).
