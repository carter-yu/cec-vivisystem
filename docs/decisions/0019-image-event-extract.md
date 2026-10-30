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

## Amendment 2026-10-30 (PR #13 review, offline)

1. **Redirects:** urllib copies the `Authorization` header onto every redirect
   it follows. The fetcher therefore refuses a redirect to anything other than
   `https://files.slack.com` before any request is sent
   (`image_fetch_refused_redirect`); checking the final URL afterwards is too
   late.
2. **Switch on, no `加event`:** only a message whose text contains `加event` is
   read with its files. Every other photo post keeps today's path exactly (a
   `file_share` stays ignored; a plain message is parsed as text only).
3. **Crossed-out, cancelled and TBC entries** come back from the model as items
   with `missing_fields ["unconfirmed"]` and are listed as not included
   (未確認／已取消). They never become the whole-list time question.
4. **Pixels:** the 40-megapixel cap is checked on the header before decoding;
   only the PNG and JPEG decoders run; the re-encoded copy carries no metadata
   (no EXIF, ICC profile or JPEG comment).
5. **Evidence for an image item** is the text the vision model returned, not the
   Slack caption. The caption (「加event」) holds none of the lines, so every
   Phase 33a line check — verified excerpt, per-item people, the all-day cue and
   the PR #11/#12 dropped-clock recovery — had nothing to read. The extractor now
   builds one NFKC-normalised line per item from its own `source_text` and passes
   it as `evidence_text`; `raw_text` stays the caption for the reply, the preview
   and narrowing. An item that returned no line has no line evidence, and a
   caption line never supplies an image item's day or clock (only the model reads
   the caption, which is in the request). A caption name may still confirm a
   model-named participant. No schema field was added: `events[]` is shared with
   the text path, which validates an exact field set.
6. **The image time question** cannot ask for "the whole list again": there is no
   typed list. It asks for the same image again with `加event` and the missing
   times in the message text, or for the typed list.

## Amendment (2026-10-30): trigger negation

The trigger is `加event` in any case, with optional spaces (`加 Event`), and it must
not come directly after a negation (optional spaces between): 唔好, 唔使, 唔駛, 唔要,
不要, 不用, 別, 咪, 無需, 毋須, don't, do not, no. English negations must start at a
word boundary. One non-negated occurrence is enough. Helper:
`image_intake.has_image_trigger` (list: `IMAGE_TRIGGER_NEGATIONS`).

## Cross-reference: image prompt v2 (Phase 33c, 2026-10-30)

`create_event_image.v2.txt` is the new default (v1 stays in the archive). It adds
the series rules the image prompt never had, matching text prompt v7: a line or
heading that only gives the schedule shared by the whole notice (a date range, a
weekday set, a daily window, 逢/every wording) and has no activity name of its own
is **not an item**; the shared range and window are copied into every class item;
one class line is one item with `series.weekdays` from its own line and
`start`/`end` at the first occurrence only; `stated_weekday` is the weekday written
on that line (null when it names more than one); `source_text` copies the class
line, never the shared schedule line; a repeating item with no end date leaves
`series.range_end` null. The rules against computing a weekday or inventing a date,
clock, person or place are unchanged.

**No schema field was added**, so amendment item 5 still holds: `events[]` is shared
with the text path, which validates an exact field set, and an image item's line
evidence is still the model's returned `source_text` (`build_image_evidence`), never
the caption. A returned `series` reaches `event_batch` only when `CEC_BATCH_SERIES`
is on (ADR 0018, "Amendment: repeating items inside a list"); with the switch off the
item is still refused as `repeating`, so the prompt is safe either way. The one
visible change with the switch off is that the shared header line stops becoming a
bogus item, which is an improvement in both modes. `CEC_IMAGE_EXTRACT` still defaults
to `off`, and an image list still takes the unchanged 33a/33c confirmation path.
