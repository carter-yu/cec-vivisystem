---
date: 2026-10-29 (Asia/Hong_Kong)
branch: feat/phase-33b-image-event-extract
model: local (Claude Code session-limited; implemented by Grok Bot from locked phase-33b doc)
goal: Events from PNG/JPEG via xAI Grok vision, reusing Phase 33a create path
constraints:
  - CEC_IMAGE_EXTRACT defaults off; xai enables B1
  - Only images attached with message text containing 加event
  - No Mini OCR; no PDF/HEIC; no thread images; no live xAI/Slack/Google in the PR
  - Do not touch Mini, Slack app, or .env; do not merge or deploy
  - Traditional Chinese UI; English ASD-STE100; archive prompts; no secrets
acceptance:
  - Offline tests cover switch off, trigger, filters, prepare, proposal header, no-events
  - uv run pytest -q and ruff clean
  - ADR 0019 records privacy consent (Carter 2026-10-29)
---

# Prompt (summary)

Implement Phase 33b per phases/phase-33b-image-event-extract.md and ADR 0019: guarded
Slack download from files.slack.com, Pillow metadata strip + resize, XaiVisionEventExtractor
with create_event_image.v1.txt and CREATE_SCHEMA_V6, wired into the plans listener behind
the kill switch, reusing the 33a confirmation writer path.

---

# Follow-up (2026-10-30, offline): evidence source and the image time question

Two defects found while reviewing PR #13.

**(a) The caption was the evidence.** `image_extract` passed the Slack caption
as `raw_text` to `_events_payload_to_parse_result`, so every Phase 33a line
check read 「加event」 instead of the schedule: `verify_source_text` failed,
`_item_participants` dropped every name, `_line_for_date` / `_recover_clock`
(PR #11/#12) never ran, and an explicit 全日 row became the whole-list time
question. The extractor now calls `build_image_evidence`, which normalises each
item's returned `source_text` (NFKC, no zero-width characters, single spaces)
into one line per item, in payload order. That text is passed as the new
`evidence_text` argument and is used wherever the text path uses the message for
line evidence; `raw_text` stays the caption for the reply, the preview and
narrowing. An item with no `source_text` contributes an empty line and therefore
has no evidence, so nothing is invented. The caption still counts for
participant names only. The same safety rules apply: one unambiguous 24-hour
clock recovers, 全日 / all day keeps the item all-day, and AM/PM or a range still
asks. No schema field was added, because `events[]` is shared with the text
path, which validates an exact field set; the image prompt now states that
`source_text` must copy the item's own line with its date and clock.

**(b) The image time question asked for the wrong thing.** It told the family to
resend the whole list, which does not exist for an image. An image-sourced
question now asks for the same image again with `加event` and the missing times
in the message text, or for the typed list (Traditional Chinese + ASD-STE100
English). The typed-list copy is unchanged.

Offline only: no xAI, Slack, Google or Mini call; the kill switch stays `off` by
default. New tests in `tests/test_image_line_evidence.py` (cases E1–E7 of the
phase test plan); ADR 0019 amendment items 5 and 6.
