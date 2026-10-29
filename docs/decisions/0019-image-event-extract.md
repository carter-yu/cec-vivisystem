# ADR 0019: Events from images via xAI Grok vision

Date: 2026-10-29. Status: Accepted and implemented offline (Phase 33b).
Decisions locked by Carter on 2026-10-29. Not deployed. Kill switch
`CEC_IMAGE_EXTRACT` defaults to `off`.

## Context

Schedules often arrive as screenshots. Before Phase 33b the bot dropped every
message with a Slack `subtype` (including `file_share`) and never downloaded
files. Phase 33a already gives a multi-event proposal path; images should reuse
it unchanged.

## Decision

1. **Pixels go to xAI Grok vision (B1).** The raw image (metadata stripped,
   downscaled) is sent with the caption. There is **no OCR on the Mini**. Carter
   accepted that everything visible in a screenshot leaves the Mini
   (2026-10-29). Mini OCR (B2) stays a recorded alternative behind the same
   `ImageEventExtractor` protocol.
2. **Trigger:** only images attached together with message text containing
   `加event`. An image without that text, and `加event` in a later message, are
   ignored for extraction.
3. **Kill switch:** `CEC_IMAGE_EXTRACT=off` (default) keeps today's behaviour
   exactly. `xai` enables B1. Turning on needs an explicit `.env` edit and a
   listener restart, authorized separately.
4. **Reuse Phase 33a:** numbered proposal, deselect, date-only time clarify,
   Calendar yes gate, per-item idempotency keys. Image proposal header:
   「從圖片讀到，請核對日期同時間 / Read from an image: check dates and times.」
5. **Guards:** PNG/JPEG only; ≤ 10 MB; ≤ 3 per message; download only from
   `files.slack.com` over HTTPS with the bot token attached after the host check;
   no cross-host redirects; Pillow re-encode without EXIF/GPS; long side ≤ 2,048 px;
   refuse > 40 megapixels. Logs carry counts only (no bytes, URLs or transcripts).
6. **Slack scope `files:read`** is required; Carter approved the app reinstall
   separately. This ADR does not authorize a Mini `.env` or deploy change.

## Consequences

- Shipping the code with the switch off is safe: no fetch, no vision call.
- A 403 on download usually means the scope is missing (see maintenance).
- Prompt injection in an image ends, at most, in a capped proposal that still
  needs yes.
