"""Phase 7 unit tests for Calendar Reader (offline, no Google)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from cec_vivisystem.calendar_reader import format_event_list, list_calendar_events
from cec_vivisystem.calendar_writer import FakeCalendarClient
from cec_vivisystem.models import (
    CalendarListedEvent,
    CalendarListOutcome,
)

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
DAY_START = datetime(2026, 9, 1, 0, 0, tzinfo=FAMILY_TZ)
DAY_END = datetime(2026, 9, 2, 0, 0, tzinfo=FAMILY_TZ)
CAL_ID = "cal-test"


def _event(event_id: str, hour: int, title: str) -> CalendarListedEvent:
    start = datetime(2026, 9, 1, hour, 0, tzinfo=FAMILY_TZ)
    return CalendarListedEvent(
        event_id=event_id,
        summary=title,
        start=start,
        end=datetime(2026, 9, 1, hour + 1, 0, tzinfo=FAMILY_TZ),
        all_day=False,
        participants=["Cedric"],
    )


def test_list_calendar_events_returns_items() -> None:
    """R1: fake list returns titles and starts."""
    client = FakeCalendarClient(
        listed_events=[_event("e1", 9, "游泳"), _event("e2", 15, "牙醫")]
    )
    result = list_calendar_events(
        time_min=DAY_START,
        time_max=DAY_END,
        client=client,
        calendar_id=CAL_ID,
        correlation_id="corr-r1",
    )
    assert result.outcome == CalendarListOutcome.SUCCESS
    assert len(result.events) == 2
    assert result.events[0].summary == "游泳"
    assert result.events[0].start.hour == 9
    assert client.list_calls == [(CAL_ID, DAY_START, DAY_END)]
    text = format_event_list(result)
    assert "2026-09-01" in text
    assert "09:00" in text
    assert "游泳" in text


def test_list_calendar_events_empty() -> None:
    """R2: empty list is success."""
    result = list_calendar_events(
        time_min=DAY_START,
        time_max=DAY_END,
        client=FakeCalendarClient(),
        calendar_id=CAL_ID,
    )
    assert result.outcome == CalendarListOutcome.SUCCESS
    assert result.events == []
    assert "(none)" in format_event_list(result)


def test_list_calendar_events_google_error() -> None:
    """R3: fake Google error → failed; no crash."""
    client = FakeCalendarClient(fail_list_with=RuntimeError("Google 403"))
    result = list_calendar_events(
        time_min=DAY_START,
        time_max=DAY_END,
        client=client,
        calendar_id=CAL_ID,
    )
    assert result.outcome == CalendarListOutcome.FAILED
    assert result.error_type == "RuntimeError"
    assert result.events == []


def test_calendar_list_result_contract_fields() -> None:
    """R4: CalendarListResult contract fields."""
    result = list_calendar_events(
        time_min=DAY_START,
        time_max=DAY_END,
        client=FakeCalendarClient(),
        calendar_id=CAL_ID,
    )
    for name in (
        "outcome",
        "calendar_id",
        "time_min",
        "time_max",
        "events",
        "error_type",
        "error_message",
        "duration_ms",
    ):
        assert hasattr(result, name), f"missing {name}"


def test_list_calendar_events_logs_boundary(capsys) -> None:
    """R5: list_attempt / list_succeeded."""
    result = list_calendar_events(
        time_min=DAY_START,
        time_max=DAY_END,
        client=FakeCalendarClient(listed_events=[_event("e1", 9, "游泳")]),
        calendar_id=CAL_ID,
    )
    assert result.outcome == CalendarListOutcome.SUCCESS
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "list_attempt" in combined
    assert "list_succeeded" in combined
