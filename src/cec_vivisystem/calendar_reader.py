"""Calendar Reader (Phase 7 + 14 recap) — list events for a time range.

Read-only. Google Calendar remains class G (no local mirror). Writer stays
the only create path. Tests inject ``FakeCalendarClient``.
``format_recap`` groups a multi-day list by HKT calendar day.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from cec_vivisystem.calendar_writer import CalendarClient
from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import (
    CalendarListedEvent,
    CalendarListOutcome,
    CalendarListResult,
)

logger = get_logger(__name__)

COMPONENT = "calendar_reader"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")


def list_calendar_events(
    *,
    time_min: datetime,
    time_max: datetime,
    client: CalendarClient,
    calendar_id: str | None = None,
    correlation_id: str | None = None,
) -> CalendarListResult:
    """List events in ``[time_min, time_max)``. Never raises on Google errors."""
    started = time.perf_counter()
    cal_id = _resolve_calendar_id(calendar_id)
    start = _normalize(time_min)
    end = _normalize(time_max)
    logger.info(
        "list_attempt",
        component=COMPONENT,
        calendar_id=cal_id,
        time_min=start.isoformat(),
        time_max=end.isoformat(),
        correlation_id=correlation_id,
    )
    try:
        events = client.list_events(calendar_id=cal_id, time_min=start, time_max=end)
    except Exception as exc:  # noqa: BLE001 — boundary: never crash the reader
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "list_failed",
            component=COMPONENT,
            outcome="failure",
            calendar_id=cal_id,
            time_min=start.isoformat(),
            time_max=end.isoformat(),
            correlation_id=correlation_id,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return CalendarListResult(
            outcome=CalendarListOutcome.FAILED,
            calendar_id=cal_id,
            time_min=start,
            time_max=end,
            events=[],
            error_type=type(exc).__name__,
            error_message=str(exc),
            duration_ms=duration_ms,
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "list_succeeded",
        component=COMPONENT,
        outcome="success",
        calendar_id=cal_id,
        time_min=start.isoformat(),
        time_max=end.isoformat(),
        correlation_id=correlation_id,
        event_count=len(events),
        duration_ms=duration_ms,
    )
    return CalendarListResult(
        outcome=CalendarListOutcome.SUCCESS,
        calendar_id=cal_id,
        time_min=start,
        time_max=end,
        events=list(events),
        duration_ms=duration_ms,
    )


def format_event_list(result: CalendarListResult) -> str:
    """Human Slack/CLI summary. Never claims a calendar write."""
    if result.outcome != CalendarListOutcome.SUCCESS:
        return "Could not read the calendar. No calendar change was made."
    day = result.time_min.astimezone(FAMILY_TZ).date() if result.time_min else None
    header = f"Events on {day.isoformat()} (Asia/Hong_Kong):" if day else "Events:"
    if not result.events:
        return header + "\n• (none)"
    lines = [header]
    for item in result.events:
        lines.append("• " + _format_item(item))
    return "\n".join(lines)


def format_recap(result: CalendarListResult) -> str:
    """Multi-day Slack/CLI recap grouped by HKT day. Never claims a write."""
    if result.outcome != CalendarListOutcome.SUCCESS:
        return "Could not read the calendar. No calendar change was made."
    if not result.events:
        return "呢段時間日曆冇活動。"
    groups: dict[date, list[CalendarListedEvent]] = defaultdict(list)
    for item in result.events:
        groups[item.start.astimezone(FAMILY_TZ).date()].append(item)
    lines: list[str] = []
    for day in sorted(groups):
        if lines:
            lines.append("")
        lines.append(day.isoformat())
        for item in sorted(groups[day], key=lambda ev: ev.start):
            lines.append("• " + _format_item(item))
    return "\n".join(lines)


def _format_item(item: CalendarListedEvent) -> str:
    title = item.summary or "(untitled)"
    if item.all_day:
        when = "all-day"
    else:
        when = item.start.astimezone(FAMILY_TZ).strftime("%H:%M")
    extra = ""
    if item.participants:
        extra = f" ({', '.join(item.participants)})"
    return f"{when} {title}{extra}"


def _resolve_calendar_id(calendar_id: str | None) -> str:
    if calendar_id is not None and calendar_id.strip():
        return calendar_id.strip()
    env_id = os.environ.get("GOOGLE_CALENDAR_ID", "").strip()
    return env_id or "primary"


def _normalize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=FAMILY_TZ)
    return value.astimezone(FAMILY_TZ)
