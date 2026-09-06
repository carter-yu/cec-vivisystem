"""Overlap / same-person warn on create proposals (Phase 8 + 13).

Reuses ``list_calendar_events`` for the proposed ``[start, end)`` window.
Proposal text is bilingual 撞期 (times + titles). Does not write, does not
hard-block, does not call freebusy, does not mirror Google Calendar locally
(class G). Tests inject FakeCalendarClient.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from cec_vivisystem.calendar_reader import list_calendar_events
from cec_vivisystem.calendar_writer import CalendarClient
from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import (
    CalendarListedEvent,
    CalendarListOutcome,
    OverlapCheckOutcome,
    OverlapCheckResult,
    OverlapHit,
    ParseResult,
)

logger = get_logger(__name__)

COMPONENT = "overlap"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
DEFAULT_DURATION = timedelta(hours=1)
DEFAULT_ALL_DAY = timedelta(days=1)


def detect_create_overlaps(
    parse_result: ParseResult,
    *,
    client: CalendarClient,
    calendar_id: str | None = None,
    correlation_id: str | None = None,
) -> OverlapCheckResult:
    """List ``[start, end)`` and return intersecting events. Never raises."""
    started = time.perf_counter()
    window = proposed_window(parse_result)
    if window is None:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "overlap_check_skipped",
            component=COMPONENT,
            outcome="skipped",
            reason="missing_start",
            correlation_id=correlation_id,
            duration_ms=duration_ms,
        )
        return OverlapCheckResult(
            outcome=OverlapCheckOutcome.SKIPPED,
            error_type="missing_start",
            error_message="overlap check skipped: parse_result.start is required",
            duration_ms=duration_ms,
        )

    time_min, time_max = window
    logger.info(
        "overlap_check_started",
        component=COMPONENT,
        calendar_id=calendar_id,
        time_min=time_min.isoformat(),
        time_max=time_max.isoformat(),
        correlation_id=correlation_id,
    )
    listed = list_calendar_events(
        time_min=time_min,
        time_max=time_max,
        client=client,
        calendar_id=calendar_id,
        correlation_id=correlation_id,
    )
    if listed.outcome != CalendarListOutcome.SUCCESS:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "overlap_check_failed",
            component=COMPONENT,
            outcome="failure",
            calendar_id=listed.calendar_id,
            time_min=time_min.isoformat(),
            time_max=time_max.isoformat(),
            correlation_id=correlation_id,
            duration_ms=duration_ms,
            error_type=listed.error_type,
            error_message=listed.error_message,
        )
        return OverlapCheckResult(
            outcome=OverlapCheckOutcome.FAILED,
            calendar_id=listed.calendar_id,
            time_min=time_min,
            time_max=time_max,
            error_type=listed.error_type,
            error_message=listed.error_message,
            duration_ms=duration_ms,
        )

    hits: list[OverlapHit] = []
    for event in listed.events:
        event_start, event_end = event_window(event)
        if not intervals_intersect(time_min, time_max, event_start, event_end):
            continue
        names = shared_participants(parse_result.participants, event.participants)
        hits.append(OverlapHit(event=event, same_person_names=names))

    duration_ms = int((time.perf_counter() - started) * 1000)
    same_person_count = sum(1 for hit in hits if hit.same_person_names)
    logger.info(
        "overlap_check_completed",
        component=COMPONENT,
        outcome="success",
        calendar_id=listed.calendar_id,
        time_min=time_min.isoformat(),
        time_max=time_max.isoformat(),
        correlation_id=correlation_id,
        overlap_count=len(hits),
        same_person_count=same_person_count,
        duration_ms=duration_ms,
    )
    return OverlapCheckResult(
        outcome=OverlapCheckOutcome.SUCCESS,
        calendar_id=listed.calendar_id,
        time_min=time_min,
        time_max=time_max,
        hits=hits,
        duration_ms=duration_ms,
    )


def proposed_window(parse_result: ParseResult) -> tuple[datetime, datetime] | None:
    """Return ``[start, end)`` in Asia/Hong_Kong, or None if start is missing."""
    if parse_result.start is None:
        return None
    start = _normalize(parse_result.start)
    if parse_result.end is not None:
        end = _normalize(parse_result.end)
        if end <= start:
            end = start + (
                DEFAULT_ALL_DAY if parse_result.all_day else DEFAULT_DURATION
            )
        return start, end
    if parse_result.all_day:
        return start, start + DEFAULT_ALL_DAY
    return start, start + DEFAULT_DURATION


def event_window(event: CalendarListedEvent) -> tuple[datetime, datetime]:
    """Return a listed event's ``[start, end)`` in Asia/Hong_Kong."""
    start = _normalize(event.start)
    if event.end is not None:
        end = _normalize(event.end)
        if end <= start:
            end = start + (DEFAULT_ALL_DAY if event.all_day else DEFAULT_DURATION)
        return start, end
    if event.all_day:
        return start, start + DEFAULT_ALL_DAY
    return start, start + DEFAULT_DURATION


def intervals_intersect(
    a_start: datetime,
    a_end: datetime,
    b_start: datetime,
    b_end: datetime,
) -> bool:
    """True iff half-open intervals ``[a)`` and ``[b)`` overlap."""
    return max(_normalize(a_start), _normalize(b_start)) < min(
        _normalize(a_end), _normalize(b_end)
    )


def shared_participants(left: list[str], right: list[str]) -> list[str]:
    """Casefold exact intersect. No aliases; do not invent emails."""
    right_keys = {
        name.strip().casefold()
        for name in right
        if isinstance(name, str) and name.strip()
    }
    found: list[str] = []
    seen: set[str] = set()
    for name in left:
        if not isinstance(name, str):
            continue
        key = name.strip().casefold()
        if key and key in right_keys and key not in seen:
            found.append(name.strip())
            seen.add(key)
    return found


def format_overlap_warning(result: OverlapCheckResult) -> str | None:
    """Warning lines to append to a proposal, or None if nothing to add."""
    if result.outcome == OverlapCheckOutcome.FAILED:
        return (
            "Warning: could not check the calendar for overlaps. "
            "You can still reply yes or no. / "
            "注意：未能檢查撞期。你仍然可以回 yes 或 不要。"
        )
    if result.outcome != OverlapCheckOutcome.SUCCESS or not result.hits:
        return None

    n = len(result.hits)
    noun = "event" if n == 1 else "events"
    lines = [
        (
            f"Warning: this proposal overlaps {n} existing {noun} / "
            f"注意：呢個提案撞期 {n} 個現有活動:"
        )
    ]
    for hit in result.hits:
        lines.append("• " + _format_hit(hit.event))
    same: list[str] = []
    seen: set[str] = set()
    for hit in result.hits:
        for name in hit.same_person_names:
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                same.append(name)
    if same:
        lines.append(
            "Warning: same person "
            + ", ".join(same)
            + " is also on overlapping event(s). / "
            "注意："
            + "、".join(same)
            + " 都喺撞期活動入面。"
        )
    return "\n".join(lines)


def _format_hit(event: CalendarListedEvent) -> str:
    title = event.summary or "(untitled)"
    start = _normalize(event.start)
    if event.all_day:
        when = "all-day"
    else:
        end = _normalize(event.end) if event.end is not None else start + DEFAULT_DURATION
        when = f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
    extra = f" ({', '.join(event.participants)})" if event.participants else ""
    return f"{when} {title}{extra}"


def _normalize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=FAMILY_TZ)
    return value.astimezone(FAMILY_TZ)
