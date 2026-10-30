---
date: 2026-10-30 (Asia/Hong_Kong)
branch: fix/image-trigger-negation
model: none (small change written directly by Grok Bot; Claude Code not used)
goal: Image extraction triggers only on a real 加event request, not a negated one
constraints:
  - One helper (image_intake.has_image_trigger); keep the change small
  - Case-insensitive 加event with optional spaces
  - No Mini, no live jobs, no merge
acceptance:
  - Positive: 加event, 請加event, 幫我加event, 加 Event, caption text plus 加event
  - Negative: 唔好加event, 唔使加 event, 不要加event, 咪加event, don't 加event, no 加event
  - uv run pytest -q and ruff clean
---

# Prompt (summary)

Carter asked to tighten the Phase 33b image trigger so negations such as 「唔好加event」
do not send an image to vision. Reject a 加event occurrence when a negation from the list
(唔好, 唔使, 唔駛, 唔要, 不要, 不用, 別, 咪, 無需, 毋須, don't, do not, no) comes directly
before it, with optional spaces. English negations need a word boundary. Negation after
the trigger is out of scope.
