---
date: 2026-10-02 HK
branch: feat/weekday-series-and-delete
model: claude-opus-5-5
goal: Implement Phase 27 (weekday series create with span guard) then Phase 28 (Calendar delete-one via confirmation) as one offline PR.
constraints: Discrete expand, not RRULE; cap 40; per-occurrence overlap; all-day multi-day clarifies; delete asks for a missing day and uses a numbered pick list when ambiguous; never silent create/delete; existing calendar.events scope only; Traditional Chinese for family-facing Chinese; no Mini deploy, no live Slack/Google writes, no secrets; do not open or merge the PR.
acceptance: Phase 27 S0–S11 and Phase 28 D1–D10 covered by offline tests; pytest and Ruff green; architecture/README/PROGRESS/ADR notes updated; prompt archive added; conventional commits.
---

# Manager request (sanitized archive)

Implement Phase 27 then Phase 28 for the family Slack + Google Calendar bot on
`feat/weekday-series-and-delete` (from origin/main). Specs are authoritative:
`phases/phase-27-weekday-series-create.md`, `phases/phase-28-calendar-delete.md`,
ADR 0011 and ADR 0012 (both Accepted), plus the ground rules and the unit-testing
standard (ADR 0003). A scratch research note informed the specs and is not
committed.

Locked defaults (not to be reopened):

1. Discrete expand (not RRULE) with a multi-day timed span guard.
2. Cap 40 occurrences; stamp `series_id` in private extendedProperties for a later
   series-delete.
3. Per-occurrence morning overlap; all-day multi-day → clarify (do not invent a
   series).
4. Delete-one: ask for the date if missing; ambiguous → numbered pick list; never
   silent delete.
5. OAuth `calendar.events` is already sufficient — no new scopes.
6. No Mini deploy; no live Slack/Google writes; no secrets in commits.
7. Traditional Chinese for user-facing Chinese copy (never Simplified).

Phase 27 required behavior: span guard for timed creates whose end date is after
the start date; detect bounded weekday series (Mon–Fri and general weekday sets +
date range + daily window); a pure injectable expander (primary case Mon–Fri
2026-10-02..30, 08:30–12:00 → 21 mornings), cap 40 with clarify over truncation,
open-ended → clarify; one Slack proposal with N, sample lines and an overflow cue;
overlap per occurrence with aggregated warnings; writer loop with child Google ids
`sha256("cec-confirmation:" + confirmation_id + "#" + local_date)`, private
parent/occurrence/series properties, class B audit per child, honest partial
counts; prompt bump v4 → v5 with series rules and schema/sanitize updates.

Phase 28 required behavior: parse delete intent (e.g. 「刪除星期四游水」) without
writing; ask for a missing date; match via list_events; unique → confirmation with
title + HKT start + short event id; none → clear empty reply; several → numbered
pick list; yes → `write_calendar_delete` → `CalendarClient.delete_event` →
`events().delete`; class B audit `op=delete`; second yes or Google 404 → soft
`already_deleted`; never delete on create reject; a model alone cannot delete.
Extend the Protocol, live client and fake with `delete_event`.

Order: Phase 27 fully, then Phase 28, then architecture/README/PROGRESS, then this
archive; run `uv run pytest -q` and `uv run ruff check .` until green; commit with
conventional commits; do not open or merge the PR and do not touch Mini.

Sanitization: local workspace paths beyond the repository, private context and
incident text omitted. Examples are synthetic. No secrets or family records.
