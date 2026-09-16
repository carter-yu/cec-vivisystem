# Phase 19 – Incident 2026-09-14-v2 (overlap reconnect + 號 dates + daily recap)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  
- [Phase 8 overlap](phase-8-overlap-warn.md) · [Phase 15 important dates](phase-15-important-dates.md) · [Phase 17 今晚](phase-17-parser-tonight-pet.md) · [Phase 18 fallback](phase-18-hybrid-parse-fallback.md)  

Parser + one Google HTTP reconnect. **Does not** change Writer gate, confirmation yes/no, or Phase 18 fallback. **No new LLM intents.**

## Goal

Family `#family-plans` lines from incident **2026-09-14-v2** work in **rules**:

1. Create-overlap check recovers from an idle `BrokenPipeError` so a 撞期 warning can appear.
2. Important dates with **號** and **加重要日子** store immediately (no yes, no calendar write).
3. Daily recap questions like **今日有咩嘢做？** list today (always a Slack reply).
4. Dated creates with **號 / 下晝3:30 / 夜晚 / 兩點** become `create_event` without needing the LLM.

## Why this phase (decision)

`incident-logs/2026-09-14-v2/`. Token ok. Phase 17 phrases work after Mini pull. New friction:

| Phrase | Parsed | What the family wanted |
|--------|--------|------------------------|
| Second create in an occupied hour | create; `overlap_check_failed` `BrokenPipeError` | 撞期 warning |
| `why cannot check overlaps?` | unknown | (meta; not a parser target) |
| `今日有咩嘢做？` | unknown (LLM also unknown) | `list_events` today |
| `加重要日子` + **號** birthday | unknown (LLM unknown — fallback is create-only) | `add_important_date` yearly |
| Year-less `9月18號` + 下晝 colon / 夜晚 / 兩點 | rules `needs_clarification`; LLM saved some creates | rules `create_event` |

Causes (rules + HTTP, not Writer):

- Cached httplib2 Google client: idle socket → `BrokenPipeError` on the next list. Overlap degrades to “could not check”. Phase 8 still allows the proposal.
- **號** is the family day suffix; rules only accepted **日**.
- `_extract_date` has no year-less `M月D日/號` (so 9月18號 has no start).
- **下晝3:30** colon time ignores 下晝 (becomes 03:30 unless 今日).
- **夜晚** / **兩點** are not clock period / hour words.
- Activity titles in the live Slack lines were not in the title table.
- List signal is **有乜**, not **有咩嘢做**.
- Important-date add requires `日` and does not strip **加重要日子**.

Grow from those lines. Do not jump to LLM. Fallback stays create-only (ADR 0005).

## Time box

1–2 hours. Green the locked fixtures and stop.

## In Scope

### 1. Parser

Keep `parse(message, *, now=, correlation_id=) -> ParseResult`.

`FIXED_NOW` for this incident: `2026-09-14 12:00 Asia/Hong_Kong`.

1. **日 / 號** are the same day suffix on month-day (and YMD / date range). Traditional only.
2. Year-less `9月18號` on **create** → that calendar date in `now`'s year; if that date is already past, next year. Important-date year policy unchanged (omitted year = yearly, `start` uses `now.year`).
3. **下晝 / 下午 / 晚上 / 夜晚** apply to bare colon times (`下晝3:30` → 15:30). Explicit am/pm still wins. 今日 1–7 afternoon rule still applies when no period word.
4. **夜晚** = evening (PM), same as 晚上.
5. Chinese hour words with 點: **兩 / 一…十二** (`下晝兩點` → 14:00). Traditional 兩 only.
6. List signal: **有咩嘢** / **有咩做** / **有咩** (after 有乜* ). `今日有咩嘢做？` / `今日有咩做？` → today `list_events`. Help `有咩指令` still help (whole-message, first). `有咩生日` still `list_important_dates` (before list).
7. Important dates: 號 works; strip leading **加重要日子** / **加個重要日子** / **加生日** from the stored title.
8. `help` lists `今日有咩嘢做？`, `9月18號`, `加重要日子：9月23號， 阿公生日`.

### 2. Google HTTP reconnect (overlap)

`GoogleCalendarClient` list and create: if the cached service raises a **stale HTTP** error (`BrokenPipeError`, `ConnectionResetError`, `RemoteDisconnected`, errno 32/54), drop `_service` and **retry the same call once**. Log `google_http_reconnect`.

Why: not a product retry loop (Phase 6/8 still one list/write attempt from overlap/writer). One reconnect of a dead idle socket.

Overlap checker, Reader, Writer contracts unchanged. Fake client in pytest still fails on injected errors (no silent swallow).

### 3. Tests

| Path | Role |
|------|------|
| `tests/test_parser.py` | V1–V10 |
| `tests/test_calendar_writer.py` | G1–G3 stale HTTP |
| `tests/test_listener.py` | L-recap, L-hao-date |

`PHASE19_NOW = 2026-09-14 12:00 Asia/Hong_Kong`.

| ID | Message / case | Expect |
|----|----------------|--------|
| **V1** | `今日有咩嘢做？` | `list_events`; today 00:00–next 00:00 |
| **V2** | `今日有咩做？` | same |
| **V3** | `加重要日子：9月23號， 阿公生日` | `add_important_date`; yearly; title contains 阿公生日 **not** 加重要日子; month 9 day 23 |
| **V4** | `9月23號， 阿公生日` | same add; yearly |
| **V5** | `加個Event，9月18號，下晝3:30 ，去公園` | `create_event`; 2026-09-18 15:30; 公園 |
| **V6** | `加個Event， 9月18號，夜晚8:30 ，Carter去公園` | `create_event`; 20:30; 公園; Carter |
| **V7** | `加個Event， 9月22日，下晝兩點，Cedric 睇牙醫` | `create_event`; 14:00; 牙醫; Cedric |
| **V8** | `今日天氣點呀` | still `unknown` |
| **V9** | I1 `3月5日 Cedric 生日` | still yearly add |
| **V10** | `今日有乜` | still `list_events` |
| **G1** | `BrokenPipeError` is stale | `is_stale_http_error` true |
| **G2** | first call BrokenPipe, second ok | `run_with_stale_http_retry` returns; reset called once |
| **G3** | `RuntimeError("Google 403")` | no retry; raises |
| **L-recap** | V1 + fake client | Slack list reply; no confirmation |
| **L-hao-date** | V3 + store | stored; ack; no confirmation; no Google create |

Named tests:

- `test_parse_today_ye_do_is_list` → V1  
- `test_parse_today_ye_do_short_is_list` → V2  
- `test_parse_add_important_date_hao_with_prefix` → V3  
- `test_parse_add_important_date_hao_birthday` → V4  
- `test_parse_hao_date_afternoon_colon_speech` → V5  
- `test_parse_hao_date_evening_haircut` → V6  
- `test_parse_two_dian_speech_training` → V7  
- `test_today_weather_still_unknown_phase19` → V8 (or reuse W7)  
- existing I1 / 今日有乜 stay green → V9, V10  
- `test_broken_pipe_is_stale_http_error` → G1  
- `test_stale_http_retry_reconnects_once` → G2  
- `test_non_stale_http_error_does_not_retry` → G3  
- `test_today_ye_do_list_replies_without_confirmation` → L-recap  
- `test_add_important_date_hao_prefix_without_confirmation` → L-hao-date  

### Non-tests

- `今日kb乜` typo
- Live Google / live Slack
- Morning recap launchd
- Teaching LLM to return `add_important_date` (rules own that intent)
- Extra participant aliases beyond Cedric / Coco / Elaine / Carter
- Hard-block on overlap
- Update/delete Writer
- Important-date edit/delete UI

## Out of Scope

| Item | Why |
|------|-----|
| Phase 18 rebuild / new LLM intents | ADR 0005; this phase promotes misses into **rules** |
| Freebusy | still deferred |
| Reminder Agent | not this incident |
| 07:00 launchd | operator; interactive recap is the gap |

## Logging

- Existing parser / overlap / reader events unchanged
- New: `google_http_reconnect` INFO on `component=calendar_writer` (error class, not tokens)
- Overlap still logs `overlap_check_failed` if the **second** list also fails

## Acceptance Criteria

- [x] This file locked before implementation
- [x] V1–V10 parse
- [x] G1–G3 stale HTTP helper
- [x] L-recap + L-hao-date
- [x] Prior suites green (I1, W1–W9, list 今日有乜, overlap O5 still fails injected errors)
- [x] Writer gate unchanged; no calendar write on important-date add
- [x] pytest + ruff clean
- [x] architecture, README, PROGRESS, logging matrix updated

## Success definition

In `#family-plans`, `今日有咩嘢做？` lists today. `加重要日子：9月23號， 阿公生日` stores. A second `今晚10點` create after an idle Google connection can still warn 撞期 when an event already sits in that hour. Dated 號 creates parse in rules.

## Explicit non-goal

Do not make the LLM handle important dates or list queries. Do not add a multi-attempt overlap retry loop. One HTTP reconnect only.
