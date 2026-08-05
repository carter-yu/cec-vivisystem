"""Phase 1 unit tests for the offline natural-language parser."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from cec_vivisystem.models import Confidence, IntentType, ParseResult
from cec_vivisystem.parser import parse

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=FAMILY_TZ)

# Locked fixture phrases (phases/phase-1-parser.md)
F1 = "星期六下午3點帶 Cedric 去游泳"
F2 = "Sunday 10am pediatrician for Cedric"
F3 = "下星期三 Elaine 睇牙醫 2:30pm"
F4 = "Add family dinner Friday 7pm at home"
F5 = "明天全日 Cedric 學校 holiday"


def _assert_create_event_common(result: ParseResult, raw: str) -> None:
    assert result.intent_type == IntentType.CREATE_EVENT
    assert result.raw_text == raw
    assert result.start is not None
    assert result.start.tzinfo is not None
    assert result.missing_fields == []
    assert result.title
    assert result.confidence in (Confidence.HIGH, Confidence.MEDIUM)


def test_parse_create_event_cantonese_saturday_swim() -> None:
    result = parse(F1, now=FIXED_NOW)
    _assert_create_event_common(result, F1)
    assert result.title is not None
    assert "游" in result.title or "swim" in result.title.lower()
    assert result.start == datetime(2026, 8, 8, 15, 0, tzinfo=FAMILY_TZ)
    assert "Cedric" in result.participants
    assert result.all_day is False


def test_parse_create_event_english_sunday_pediatrician() -> None:
    result = parse(F2, now=FIXED_NOW)
    _assert_create_event_common(result, F2)
    assert result.title is not None
    assert "pediatrician" in result.title.lower()
    assert result.start == datetime(2026, 8, 9, 10, 0, tzinfo=FAMILY_TZ)
    assert "Cedric" in result.participants


def test_parse_create_event_mixed_next_wednesday_dentist() -> None:
    result = parse(F3, now=FIXED_NOW)
    _assert_create_event_common(result, F3)
    assert result.start == datetime(2026, 8, 12, 14, 30, tzinfo=FAMILY_TZ)
    assert "Elaine" in result.participants
    assert result.title is not None
    assert "醫" in result.title or "dentist" in result.title.lower()


def test_parse_create_event_english_friday_dinner() -> None:
    result = parse(F4, now=FIXED_NOW)
    _assert_create_event_common(result, F4)
    assert result.start == datetime(2026, 8, 14, 19, 0, tzinfo=FAMILY_TZ)
    assert result.location is not None
    assert "home" in result.location.lower()


def test_parse_create_event_all_day_tomorrow_school_holiday() -> None:
    result = parse(F5, now=FIXED_NOW)
    _assert_create_event_common(result, F5)
    assert result.all_day is True
    assert result.start is not None
    assert result.start.astimezone(FAMILY_TZ).date() == datetime(
        2026, 8, 9, tzinfo=FAMILY_TZ
    ).date()
    assert result.title is not None
    title_l = result.title.lower()
    assert "學校" in result.title or "school" in title_l or "holiday" in title_l


def test_parse_needs_clarification_missing_time() -> None:
    text = "幫我 book 游泳"
    result = parse(text, now=FIXED_NOW)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert result.raw_text == text
    assert any("start" in f or "time" in f or "date" in f for f in result.missing_fields)
    assert result.start is None


def test_parse_needs_clarification_missing_what() -> None:
    text = "星期六下午3點"
    result = parse(text, now=FIXED_NOW)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert any("title" in f or "what" in f for f in result.missing_fields)
    assert result.intent_type != IntentType.CREATE_EVENT


def test_parse_unknown_not_create_event() -> None:
    text = "今日天氣點呀"
    result = parse(text, now=FIXED_NOW)
    assert result.intent_type == IntentType.UNKNOWN


def test_parse_unknown_empty_or_whitespace() -> None:
    result = parse("   ", now=FIXED_NOW)
    assert result.intent_type == IntentType.UNKNOWN


def test_parse_result_has_contract_fields() -> None:
    result = parse(F1, now=FIXED_NOW)
    for name in (
        "intent_type",
        "title",
        "start",
        "end",
        "all_day",
        "location",
        "participants",
        "raw_text",
        "confidence",
        "missing_fields",
        "notes",
    ):
        assert hasattr(result, name), f"missing contract field: {name}"


def test_parse_does_not_raise_on_garbage() -> None:
    result = parse("???!!! 😅", now=FIXED_NOW)
    assert result.intent_type in (
        IntentType.UNKNOWN,
        IntentType.NEEDS_CLARIFICATION,
    )


def test_parse_logs_boundary() -> None:
    """Boundary logging is configured; parse must complete cleanly."""
    result = parse(F1, now=FIXED_NOW)
    assert result.intent_type == IntentType.CREATE_EVENT
