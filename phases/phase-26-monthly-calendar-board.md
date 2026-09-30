# Phase 26: Morning monthly calendar board (PNG) + keep today text

Date: 2026-10-01. Scope: one offline design→implementation slice, one PR, no Mini deploy.
Inherits ground rules, unit-testing standard, logging/retention, resilience, architecture.
Builds on Phase 12 morning recap and Phase 25 scheduled-post reconciliation.

## Why

Family (Elaine) wants a **full-month visual calendar** in Slack: every day of the
current month as a cell, each cell listing timed events. Keep the existing 07:00
**today text** recap; add the monthly PNG in the **same 07:00 job**.

## Locked product decisions (2026-10-01)

| Decision | Choice |
| --- | --- |
| Schedule | Same 07:00 Asia/Hong_Kong job as morning recap |
| Today text | **Keep** existing today list / empty wording |
| Month image | **Add** full-month board PNG to Slack |
| Week start | **Sunday → Saturday** (日一二三四五六) |
| Writes | None — read-only Google list + Slack post |
| Deploy | Offline PR only; Mini pull/restart needs later explicit auth |

## Goal

1. At 07:00 HKT, after (or with) today’s text recap, post one PNG of the **current
   calendar month** into the plans channel.
2. Cells show day-of-month and up to **4** event lines (`HH:MM title` or all-day);
   overflow as `+N more`.
3. Pure, injectable render + delivery; offline tests never hit Slack/Google.
4. Image failure **must not** undo a successful today-text post (separate outcomes /
   markers as needed).
5. Docs + prompt archive; no Calendar Writer / parser / confirmation / LLM changes.

## Layout specification (v1 — implement exactly)

### Canvas

- Format: PNG, RGB (no alpha required for Slack).
- Size: **1680 × 1260** px (±40 px OK if grid math needs it; keep ~4:3).
- Background: paper `#F7F3EE`.
- Outer margin: ~48 px. Card may use soft rounded clip; optional 1 px border `#E5DFD6`.

### Header (top band ~96 px)

- Left: `{YYYY}年{M}月` (e.g. `2026年10月`), ~36–40 px, color `#2C2622`.
- Right (smaller): `家庭日曆 · 07:00 更新` (HKT implied), color `#6B5E55`.
- Thin rule under header: `#E5DFD6`.

### Weekday row

- Seven equal columns, labels in Traditional Chinese: **日 一 二 三 四 五 六**.
- Weekend columns (日、六): light cool wash `#EEF1F4`.
- Weekday columns: same paper or `#FAF7F2`.
- Label centered, ~14–16 px, `#6B5E55`.

### Month grid

- Up to **6 rows × 7 columns** covering the month with Sunday-first alignment.
- Leading / trailing days of adjacent months: show muted day numbers
  (`#B5AEA6`) on `#F0EBE4`; **no** event lines for those cells in v1
  (events only for days in the target month).
- Cell border: `#E5DFD6`, ~1 px.
- **Today** (HKT date of `now`): fill `#FFE8D6`, left accent bar or outer stroke `#E8A87C`,
  day number bold.

### Per-cell content

```
┌──────────────────┐
│               15 │  day number top-right
│ 全日 學校假期     │  all-day: soft pill background
│ 09:30 游水        │  timed: time then title
│ 14:00 體能班      │
│ 16:30 Miss Wong… │  truncate with …
│           +2 more│  if > 4 event lines
└──────────────────┘
```

Rules:

1. Sort: all-day first, then by start time ascending (HKT).
2. Timed line: `HH:MM` (24h) + space + title; time never truncated.
3. All-day line: prefix `全日` (or equivalent short label) + title.
4. Max **4** event lines per in-month cell; if more, 4th visible lines kept and
   append `+N more` where N = hidden count (may replace the 4th line if needed
   so the overflow cue is always visible when N≥1).
5. Title truncate to fit cell width (~8–10 CJK glyphs at ~10–11 px); ellipsis.
6. Multi-day events: appear on each occupied in-month day; non-start days may
   prefix `→` (reuse reader “continued” idea). Exclusive end semantics match
   `format_recap` / Google list window.
7. Empty in-month day: day number only (breathing room).
8. Untitled: `(untitled)` — never invent titles.

### Typography

- Prefer bundled **Noto Sans TC** under `assets/fonts/` for reproducible CI/workshop.
- Live Mini may use PingFang TC if present; code must resolve font path with
  explicit fallback order and fail with a clear log if none load (no tofu spam).
- Contrast: body text `#2C2622` / `#6B5E55` on paper; no light-gray-on-cream.

### Color (v1 single accent)

| Role | Hex |
| --- | --- |
| Paper | `#F7F3EE` |
| Grid / rules | `#E5DFD6` |
| Ink | `#2C2622` |
| Muted | `#6B5E55` |
| Today fill | `#FFE8D6` |
| Today accent | `#E8A87C` |
| Weekend wash | `#EEF1F4` |
| Adjacent-month | `#F0EBE4` / `#B5AEA6` |

No per-person or category colors in v1.

## Architecture

### Components

1. **`calendar_board` (new)** — pure render:
   - `build_month_cells(month: date, events, *, today: date, week_start=SUNDAY) -> grid model`
   - `render_month_board_png(model, *, font_path=...) -> bytes`
   - No I/O, no Slack, no env reads inside pure functions.
2. **`morning_recap` (extend)** — orchestration:
   - Keep existing today text path + Phase 25 delivery reservation.
   - After text success (or in parallel design: text first), list
     `[month_start, next_month_start)` via `list_calendar_events`, render PNG,
     upload to Slack with short caption (e.g. `今個月日曆一覽（上圖）` or
     filename-only caption — exact string in implementation, Traditional Chinese).
3. **Slack poster** — extend protocol beyond text-only:
   - Add injectable `post_file(*, channel_id, text, filename, content: bytes) -> SlackPostReceipt`
     (or equivalent) using Slack `files_upload_v2` (or current recommended upload API).
   - `FakeSlackPoster` records bytes + metadata; never networks.
   - Existing text `post` remains for today recap and other jobs.

### Delivery / idempotency

- Today text: existing morning-recap marker / Phase 25 rules unchanged.
- Month board: **separate** delivery key (e.g. `monthly_board` + calendar month
  `YYYY-MM`, or recap_date + artifact kind) so:
  - Re-run same day skips duplicate image if already posted successfully.
  - Text posted + image failed → rerun may post image only (text still skipped).
  - Image posted + text failed → follow text path rules independently.
- Prefer reserve-before-upload for the file path consistent with ADR 0009 spirit;
  document chosen marker shape in a short ADR if file upload needs new fields
  (filename, slack file id optional — do not store event bodies).

### Config / ops

- Same channel as morning recap (`SLACK_PLANS_CHANNEL_ID` or existing env).
- CLI: extend morning recap `main()` or add flag/`monthly_board.main` documented
  in README; launchd remains the existing 07:00 agent once Mini is authorized later.
- Fonts: commit license-compatible Noto Sans TC subset or full OTFs under
  `assets/fonts/` + README note; git LFS only if required by size policy.

### Logging

- `component=calendar_board` or `morning_recap` with distinct event names:
  `month_board_render_*`, `month_board_upload_*`, `month_board_skipped`,
  `month_board_failed`.
- Log month, event_count, png_bytes, duration_ms, correlation_id.
- Never log tokens, file contents as base64 dumps, or PII beyond existing list norms.

## In scope

- Layout above; Sunday-first week.
- Pillow dependency + font asset.
- Slack file upload path + fakes.
- Wire into 07:00 morning job; keep today text.
- Offline unit tests (table below).
- Phase docs, architecture blurb, PROGRESS, `prompts/` archive for the PR.
- Optional short ADR: “PNG board via Pillow + Slack files upload; text and image
  delivery markers independent.”

## Out of scope

| Item | Notes |
| --- | --- |
| Calendar update/delete | Separate amend phase |
| Important-date edit, freebusy, LLM | Unchanged |
| Per-person colors, week view, PDF | Later |
| Interactive Slack Block Kit calendar | Later |
| Replacing today text | Explicitly not chosen |
| Auto Mini deploy / launchd edit | Needs later explicit auth |
| Silent calendar writes | Never |

## Unit test plan (locked before code)

Fixed `now = 2026-10-01 07:00 Asia/Hong_Kong` unless noted. Fake calendar + fake poster.
No network. Prefer structural asserts on grid model; optional PNG magic-bytes /
size bounds; golden PNG optional if stable.

| ID | Scenario | Expect |
| --- | --- | --- |
| G1 | October 2026 grid, week starts Sunday | First cell is Sep 27 (or correct Sun); Oct 1 under 四 |
| G2 | Today highlight | 2026-10-01 cell marked today |
| G3 | Timed + all-day sort | All-day above timed; times ascending |
| G4 | >4 events one day | Exactly overflow cue with correct N |
| G5 | Empty month | Grid renders; no crash; empty cells OK |
| G6 | Multi-day spanning | Appears on each in-month day; `→` on non-start |
| G7 | Adjacent-month cells | No event lines |
| R1 | `render_month_board_png` | Non-empty PNG bytes; width/height ≈ spec |
| R2 | Missing font | Controlled failure / clear error path (no hang) |
| M1 | Morning run: text + image success | Text post + file post; both markers |
| M2 | Text OK, upload fail | Text marker posted; image failed/pending per policy; no calendar write |
| M3 | Second run same day | Skip both (or skip each already completed) |
| M4 | List month failure | Image failed; today text still attempted/independent |
| M5 | Existing empty today wording | Unchanged when no today events |

Keep Phases 0–25 suites green. Ruff clean. `uv lock` updated for Pillow.

## Acceptance criteria

- [x] Layout matches locked table (Sunday-first, 4-line cap, colors, canvas)
- [x] 07:00 path keeps today text and adds month PNG upload
- [x] Image failure does not claim text failure incorrectly; markers independent
- [x] Fake poster covers file uploads; pytest green offline
- [x] Pillow + font path documented; no secrets in repo
- [x] architecture / README / PROGRESS / prompts archive updated
- [x] No Writer/parser/confirmation/LLM changes; no Mini deploy in this PR

## Success definition

Family opens Slack after 07:00 and sees today’s short list **and** a readable,
attractive full-month board for the current month (Sunday-first), without
anyone editing Calendar by hand for this feature.

## Implementation notes (for Codex later)

- Prefer `gpt-6-astra` or current guide pick for multi-file layout + delivery.
- Working tree: `/workspace/cec-vivisystem` on Grok Bot computer.
- Branch naming: e.g. `feat/monthly-calendar-board`.
- Do not push Mini changes; do not merge without Carter review.

## Open points resolved

- Week start: **Sunday** (Carter, 2026-10-01).
- Product: **text + image** same job (Carter, 2026-10-01).

## Related parked work

Calendar event amend (update/delete via confirmation) remains a **separate**
phase; not blocked by this board, not included here.

## Implemented marker choice and font boundary

Board markers use the permitted recap-date + artifact-kind option:
`data/morning_recap/monthly_board/YYYY-MM-DD.json`. This refreshes the month view
each day while skipping same-day completed uploads. Both stores retain 30 days.
See ADR 0010 for file-ID receipt semantics and manual reconciliation.
Font loading is the only renderer filesystem access. The bundled subset omits
U+2192; continuation arrows are drawn as strokes. Synthetic PNG inspection and
structural tests cover the layout without storing live Calendar contents.
