---
date: 2026-10-29 (Asia/Hong_Kong)
branch: fix/phase-33a-time-detect
model: local (no Claude Code run)
goal: Recover a start clock the v6 model dropped on a dated list line
constraints:
  - Do not invent a clock; read only from the item's own verified line (or the single message line naming that date)
  - One unambiguous 24-hour HH:MM only; ranges and AM/PM / 上午下午 cues keep the time question
  - No Mini deploy, Slack config, or Google re-auth in the coding step
  - Offline tests only
acceptance:
  - Carter's 2026-10-29 live five-line paste (times like 15:30 / 14:00) becomes a five-item proposal
  - Lines with no clock, a range, or a period cue still ask for time
  - uv run pytest -q and ruff clean
---

# Prompt (summary)

After PR #10 deployed with v6 as the default create prompt, Carter pasted a five-line therapy list in #family-plans. Every line had HH:MM. The bot replied that every line had a date but no time. Mini logs showed v6 + grok-4.20-0309-non-reasoning, `batch_extract_completed` with `missing_time:5`. Offline, the same payload with start filled maps correctly; with start null and the clock on the line, the bug reproduces.

Fix: in `_events_payload_to_parse_result`, when mapped.start is null and a local date is known, call `_recover_clock` to read exactly one 24-hour clock from the item's source line (or the unique message line for that date). Document the live UAT fail in the function docstring. Add `tests/test_batch_clock_recovery.py`.
