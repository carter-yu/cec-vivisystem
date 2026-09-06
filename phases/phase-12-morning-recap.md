# Phase 12 – Morning today-recap (07:00) + list must-reply

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

Thin scheduled recap of **today only**. Also fixes the 2026-09-05 silence on `聽日有乜嘢活動`. Does **not** waive system standards.

## Goal

1. Review redacted 2026-09-05 logs (Incident A: no Slack reply).
2. Phrases like `聽日有乜嘢活動` are `LIST_EVENTS` and the Listener **always replies** (list, empty, or explicit error — never silent).
3. A **07:00 Asia/Hong_Kong** job posts today’s events to `#family-plans` (or configured plans channel). Empty day still posts so the family knows it ran.
4. Offline-testable with injectable `now=` and fake calendar + fake Slack poster.

No period recap (week/month). No overlap rewrite. No important-dates. No LLM. No freebusy. No calendar write.

## Why this phase (decision)

Simplest upgrade after live use: Phase 7 already lists one calendar day. A morning post is that primitive on a clock. If list questions stay silent, the 07:00 job would fail the same way — fix the read path first.

Incident B (duplicate creates) and period recap wait for Phases 13–14.

## Time box

1–2 hours. Prefer: P0 parse + must-reply + `run_morning_recap` tests + CLI. launchd plist on Mini is **operator stretch** (document the command).

## Operator artifacts

Redacted excerpts in `incidents/2026-09-05/` (committed) or gitignored `logs/` on Mini. No tokens, no `.env`.

Look for `message_received`, `message_ignored`, `dispatch_succeeded`, `dispatch_failed`, `intent_type` around `聽日有乜嘢活動`.

## In Scope

### 1. Parser — must be list, not create/unknown

| ID | Message | Window (HKT, end exclusive) |
|----|---------|-----------------------------|
| P0 | `聽日有乜嘢活動` | tomorrow 00:00 → next 00:00 |
| P0b | `聽日有乜嘢` / `聽日有什麼活動` | same |
| Q3 | existing `聽日有乜` | unchanged |

`_try_list_events` stays before create. These must **not** become `create_event` or `unknown`.

### 2. Listener — schedule questions are never silent

`LIST_EVENTS`: always a Slack reply if the message was accepted (right channel).  
Google/client/`start` problems → explicit error line, **still a reply**.  
Keep `No calendar change was made` (or equivalent) on read-only replies.

### 3. Morning recap component

New module e.g. `src/cec_vivisystem/morning_recap.py`.

`run_morning_recap(*, now=, client=, poster=, calendar_id=, channel_id=, store=) -> MorningRecapResult`

- Window: local calendar **today** `[00:00, next 00:00)` HKT from `now`.
- Reuse `list_calendar_events` + existing `format_event_list` (or a thin today header + that formatter).
- Empty: still post, e.g. `早晨。今日（YYYY-MM-DD）日曆冇活動。`
- Non-empty: header `早晨。今日（YYYY-MM-DD）活動：` + list.
- **Idempotent:** one successful post per calendar date; second run `skipped_already_posted` (JSON under gitignored `data/morning_recap/` is enough).
- `poster` injectable; tests never hit Slack.
- Failures: log `component=morning_recap`; do not write calendar.

**CLI:** `uv run python -c "from cec_vivisystem.morning_recap import main; main()"`  
Operator Mini: launchd at 07:00 HKT calling that command (document in PROGRESS / README; plist not required for pytest acceptance).

### Logging

- `component=morning_recap`: `morning_recap_started` / `morning_recap_posted` / `morning_recap_skipped` / `morning_recap_failed`
- Listener list path: existing events plus a reply on failure (no more silent `dispatch_failed` without a user-visible line — if failure is before reply, still attempt an error reply)

### Docs

- architecture: Morning recap = scheduled today-list, not an orchestrator
- README quick start + operator 07:00 note
- PROGRESS: Incident A conclusion + Phase 12 result

## Out of Scope

| Item | Phase |
|------|--------|
| Duplicate Google create / bilingual 撞期 | 13 |
| 今個星期／今個月／date range recap | 14 |
| Important dates + 10:00 next-week review | 15 |
| Freebusy, LLM, update/delete | later |
| Hard-block overlaps | never in 12 |

## Unit test plan (locked)

`FIXED_NOW = 2026-09-05 12:00 Asia/Hong_Kong` unless the morning test uses `07:00`. Fake calendar + fake poster. No network.

| ID | Scenario | Expect |
|----|----------|--------|
| P0 | `聽日有乜嘢活動` | `list_events`, tomorrow window, not create/unknown |
| Q3 | `聽日有乜` | still list_events |
| L1 | listener P0 + fake client | Slack reply; no confirmation |
| L2 | listener list + Google error | **reply** with error; no write |
| M1 | `run_morning_recap` with two events | one poster call; today window |
| M2 | empty calendar | still one poster call; empty wording |
| M3 | second run same date | skip; no second post |
| M4 | list failure | failed outcome; no calendar write |

Keep Phases 0–11 suites green.

## Acceptance Criteria

- [ ] Incident A logs reviewed; PROGRESS names the hypothesis
- [ ] P0/P0b parse + listener reply
- [ ] `run_morning_recap` M1–M4
- [ ] CLI documented
- [ ] No week/month recap, no Writer change, no 10:00 job
- [ ] pytest + ruff clean
- [ ] architecture, README, PROGRESS updated

## Success definition

At 07:00 the family gets today’s events (or a clear empty post). Asking `聽日有乜嘢活動` in `#family-plans` always gets a recap-style reply.