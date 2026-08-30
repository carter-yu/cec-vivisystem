"""Phase 8 unit tests for overlap / same-person warn (offline, no Google)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from cec_vivisystem.calendar_writer import FakeCalendarClient
from cec_vivisystem.models import (
    CalendarListedEvent,
    Confidence,
    IntentType,
    OverlapCheckOutcome,
    ParseResult,
)
from cec_vivisystem.overlap import detect_create_overlaps
from cec_vivisystem.parser import parse

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=FAMILY_TZ)
F1 = "星期六下午3點帶 Cedric 去游泳"
CAL_ID = "cal-test"
WINDOW_START = datetime(2026, 8, 8, 15, 0, tzinfo=FAMILY_TZ)
WINDOW_END = datetime(2026, 8, 8, 16, 0, tzinfo=FAMILY_TZ)


def _f1() -> ParseResult:
    result = parse(F1, now=FIXED_NOW)
    assert result.intent_type == IntentType.CREATE_EVENT
    assert result.start == WINDOW_START
    return result


def _listed(
    *,
    event_id: str,
    hour: int,
    duration_hours: int = 1,
    title: str = "牙醫",
    participants: list[str] | None = None,
) -> CalendarListedEvent:
    start = datetime(2026, 8, 8, hour, 0, tzinfo=FAMILY_TZ)
    return CalendarListedEvent(
        event_id=event_id,
        summary=title,
        start=start,
        end=datetime(2026, 8, 8, hour + duration_hours, 0, tzinfo=FAMILY_TZ),
        all_day=False,
        participants=list(participants or []),
    )


def test_overlapping_interval_and_same_person() -> None:
    """O1: 15:30–16:30 牙醫 (Cedric) overlaps F1 and is same-person."""
    client = FakeCalendarClient(
        listed_events=[
            CalendarListedEvent(
                event_id="e-dentist",
                summary="牙醫",
                start=datetime(2026, 8, 8, 15, 30, tzinfo=FAMILY_TZ),
                end=datetime(2026, 8, 8, 16, 30, tzinfo=FAMILY_TZ),
                all_day=False,
                participants=["Cedric"],
            )
        ]
    )
    result = detect_create_overlaps(
        _f1(),
        client=client,
        calendar_id=CAL_ID,
        correlation_id="corr-o1",
    )
    assert result.outcome == OverlapCheckOutcome.SUCCESS
    assert result.time_min == WINDOW_START
    assert result.time_max == WINDOW_END
    assert len(result.hits) == 1
    assert result.hits[0].event.summary == "牙醫"
    assert result.hits[0].same_person_names == ["Cedric"]
    assert client.list_calls == [(CAL_ID, WINDOW_START, WINDOW_END)]
    assert client.calls == []


def test_adjacent_interval_is_not_overlap() -> None:
    """O2: adjacent [16:00, 17:00) is not an overlap of [15:00, 16:00)."""
    client = FakeCalendarClient(
        listed_events=[
            _listed(event_id="e-adj", hour=16, title="鋼琴", participants=["Cedric"])
        ]
    )
    result = detect_create_overlaps(_f1(), client=client, calendar_id=CAL_ID)
    assert result.outcome == OverlapCheckOutcome.SUCCESS
    assert result.hits == []


def test_zifan_is_not_cedric() -> None:
    """O3: 梓梵 on an overlapping event is not same-person with Cedric."""
    client = FakeCalendarClient(
        listed_events=[
            _listed(
                event_id="e-swim",
                hour=15,
                title="游水",
                participants=["梓梵"],
            )
        ]
    )
    result = detect_create_overlaps(_f1(), client=client, calendar_id=CAL_ID)
    assert result.outcome == OverlapCheckOutcome.SUCCESS
    assert len(result.hits) == 1
    assert result.hits[0].same_person_names == []


def test_no_listed_events_is_empty() -> None:
    """O4: empty or far-away events → no hits."""
    empty = detect_create_overlaps(
        _f1(),
        client=FakeCalendarClient(),
        calendar_id=CAL_ID,
    )
    assert empty.outcome == OverlapCheckOutcome.SUCCESS
    assert empty.hits == []

    far = detect_create_overlaps(
        _f1(),
        client=FakeCalendarClient(
            listed_events=[
                _listed(event_id="e-far", hour=9, title="早餐", participants=["Cedric"])
            ]
        ),
        calendar_id=CAL_ID,
    )
    assert far.outcome == OverlapCheckOutcome.SUCCESS
    assert far.hits == []


def test_list_error_returns_failed_without_crash() -> None:
    """O5: fake Google list error → failed; does not raise."""
    client = FakeCalendarClient(fail_list_with=RuntimeError("Google 403"))
    result = detect_create_overlaps(_f1(), client=client, calendar_id=CAL_ID)
    assert result.outcome == OverlapCheckOutcome.FAILED
    assert result.error_type == "RuntimeError"
    assert result.hits == []


def test_overlap_result_contract_fields() -> None:
    """O6: OverlapCheckResult contract fields."""
    result = detect_create_overlaps(
        _f1(),
        client=FakeCalendarClient(),
        calendar_id=CAL_ID,
    )
    for name in (
        "outcome",
        "time_min",
        "time_max",
        "hits",
        "error_type",
        "error_message",
        "duration_ms",
    ):
        assert hasattr(result, name), f"missing {name}"


def test_overlap_logs_boundary(capsys) -> None:
    """O7: overlap_check_started / overlap_check_completed."""
    result = detect_create_overlaps(
        _f1(),
        client=FakeCalendarClient(
            listed_events=[
                CalendarListedEvent(
                    event_id="e-dentist",
                    summary="牙醫",
                    start=datetime(2026, 8, 8, 15, 30, tzinfo=FAMILY_TZ),
                    end=datetime(2026, 8, 8, 16, 30, tzinfo=FAMILY_TZ),
                    all_day=False,
                    participants=["Cedric"],
                )
            ]
        ),
        calendar_id=CAL_ID,
    )
    assert result.outcome == OverlapCheckOutcome.SUCCESS
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "overlap_check_started" in combined
    assert "overlap_check_completed" in combined


def test_missing_start_skips_without_list() -> None:
    """O8: missing start → skipped; no list call."""
    client = FakeCalendarClient()
    parsed = ParseResult(
        intent_type=IntentType.CREATE_EVENT,
        title="游泳",
        start=None,
        end=None,
        all_day=False,
        location=None,
        participants=["Cedric"],
        raw_text=F1,
        confidence=Confidence.MEDIUM,
    )
    result = detect_create_overlaps(parsed, client=client, calendar_id=CAL_ID)
    assert result.outcome == OverlapCheckOutcome.SKIPPED
    assert client.list_calls == []
    assert result.hits == []
