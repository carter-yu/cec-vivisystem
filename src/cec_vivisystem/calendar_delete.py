"""Delete-one target matching (Phase 28, ADR 0012) — read-only, no writes.

Why: the family must see exactly which Google event a delete would remove.
Contract: candidates come only from a Calendar list for one HKT day
(``[00:00, next 00:00)``); matching is deterministic (title alias/substring,
optional clock, participant preference) and never invents an event. The
Writer deletes only after an accepted confirmation with one target.

Do not: widen the window silently when the day is missing (the parser asks),
or let a model choose the target.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from cec_vivisystem.models import (
    CalendarDeleteTarget,
    CalendarListedEvent,
    IntentType,
    ParseResult,
)
from cec_vivisystem.parser import canonical_title

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
# Single-digit numbered pick in Slack; more matches → ask to narrow.
MAX_DELETE_CANDIDATES = 9


def delete_window(parse_result: ParseResult) -> tuple[datetime, datetime] | None:
    """The HKT calendar day named by a delete request, or None."""
    if parse_result.intent_type != IntentType.DELETE_EVENT or parse_result.start is None:
        return None
    start = _normalize(parse_result.start).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def match_delete_candidates(
    parse_result: ParseResult,
    events: list[CalendarListedEvent],
    *,
    calendar_id: str | None,
) -> list[CalendarDeleteTarget]:
    """Rank listed events against delete hints; deterministic order."""
    title = (parse_result.title or "").strip()
    wants_time = "time" in (parse_result.notes or "").split(";")
    matched = [event for event in events if _title_matches(title, event)]
    if wants_time and parse_result.start is not None:
        clock = _normalize(parse_result.start).strftime("%H:%M")
        matched = [
            event for event in matched
            if not event.all_day and _normalize(event.start).strftime("%H:%M") == clock
        ]
    if parse_result.participants:
        # Preference, not a hard filter: older events may lack participant text.
        with_people = [event for event in matched if _has_participants(parse_result, event)]
        if with_people:
            matched = with_people
    matched.sort(key=lambda e: (_normalize(e.start), e.summary or "", e.event_id))
    return [
        CalendarDeleteTarget(
            event_id=event.event_id,
            calendar_id=calendar_id,
            summary=event.summary,
            start=_normalize(event.start),
            end=_normalize(event.end) if event.end is not None else None,
            all_day=event.all_day,
        )
        for event in matched
    ]


def _title_matches(title: str, event: CalendarListedEvent) -> bool:
    if not title:
        return True
    summary = (event.summary or "").strip()
    if not summary:
        return False
    wanted = title.casefold()
    have = summary.casefold()
    if wanted in have or (len(have) >= 2 and have in wanted):
        return True
    alias = canonical_title(summary)
    return alias is not None and alias.casefold() == wanted


def _has_participants(parse_result: ParseResult, event: CalendarListedEvent) -> bool:
    known = {p.casefold() for p in event.participants}
    summary = (event.summary or "").casefold()
    return all(
        name.casefold() in known or name.casefold() in summary
        for name in parse_result.participants
    )


def _normalize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=FAMILY_TZ)
    return value.astimezone(FAMILY_TZ)
