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
