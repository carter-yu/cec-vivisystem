---
date: 2026-11-11 (Asia/Hong_Kong)
branch: fix/important-date-create-cue
model: claude-opus-5-5 via Claude Code
goal: A message with an explicit create cue and a clock range becomes a Calendar create proposal, not an immediate important date
constraints:
  - Focused fix in parser.py (plus the parse ingress if needed); no new create vocabulary
  - Reuse the image-trigger negation idea; 唔好加event is not a create cue
  - Keep mentions, channels and URLs in Slack markup unchanged
  - Leave AGENTS.md alone; no Mini, no live jobs, no merge, no push, no PR
acceptance:
  - Synthetic incident 「幫我加個event 11月28日 0900 - 1500 親子旅行」, raw and with Slack <tel:…|…> markup, is not add_important_date
  - 「11月28日 親子旅行」 is still add_important_date
  - 「11月28日 0900-1500 親子旅行」 has a recognised start clock
  - 「加event 12月3日 考試」 takes the create path
  - 0960-1500 and 2500-2600 are not clocks; a phone number without a date is unchanged
  - A negated cue alone does not force the create path
  - uv run pytest -q and ruff clean
---

# Prompt (summary)

Carter reported that 「幫我加個event 11月28日 0900 - 1500 親子旅行」 was saved as a
yearly important date. Slack delivered the clock range as phone link markup
(`<tel:0900-1500|0900 - 1500>`). Diagnosed cause: `_parse_control_impl` calls
`_try_add_important_date` before the model; that helper fires on month-day + keyword
(旅行) with no clock, `_extract_time` did not read `HHMM-HHMM`, and there was no
create-cue check.

Fix: (a) `_try_add_important_date` returns None when a non-negated create cue is
present (加event, 加個event, add event, add calendar event, 加入日曆; any case,
optional spaces). (b) `_extract_time` reads a 24h range `HHMM [-–至到] HHMM`
(hours 00–23, minutes 00–59, end after start) and returns the start. (c) Slack
`<tel:X|Y>` and `<mailto:X|Y>` become Y at the parse entry points (`parse`,
`parse_control`, `parse_with_fallback`), so rules, the model and `raw_text` read the
typed text; life-note raw text is untouched. (d) A cue prefix left on an
important-date title (negated cue) is stripped.
