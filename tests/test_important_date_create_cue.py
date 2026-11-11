"""Important-date route vs explicit create cue (Carter 2026-11-11).

Synthetic incident shape: 「幫我加個event 11月28日 0900 - 1500 親子旅行」 was
saved as a yearly important date. Slack also auto-links the clock range as a
phone number (``<tel:…|0900 - 1500>``).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.models import IntentType
from cec_vivisystem.parse_fallback import FakePayloadLlmParser, parse_with_fallback
from cec_vivisystem.parser import (
    _extract_time,
    has_create_cue,
    normalize_slack_markup,
    parse,
    parse_control,
)

TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=TZ)
INCIDENT = "幫我加個event 11月28日 0900 - 1500 親子旅行"
INCIDENT_SLACK = "幫我加個event 11月28日 <tel:0900-1500|0900 - 1500> 親子旅行"


@pytest.mark.parametrize("text", [INCIDENT, INCIDENT_SLACK])
def test_incident_is_not_an_important_date(text):
    assert parse_control(text, now=NOW) is None
    # Offline rules read the day and clock; the title is the model's job.
    result = parse(text, now=NOW)
    assert result.intent_type != IntentType.ADD_IMPORTANT_DATE
    assert result.start == datetime(2026, 11, 28, 9, 0, tzinfo=TZ)
    assert result.raw_text == INCIDENT


def test_incident_slack_form_reaches_model_as_display_text():
    payload = {
        "intent_type": "create_event", "title": "親子旅行",
        "start": "2026-11-28T09:00:00+08:00", "end": "2026-11-28T15:00:00+08:00",
        "all_day": False, "location": None, "participants": [],
        "missing_fields": [], "confidence": "medium", "series": None,
    }
    llm = FakePayloadLlmParser(payload)
    result = parse_with_fallback(INCIDENT_SLACK, now=NOW, llm=llm, llm_first=True)
    assert llm.calls == [INCIDENT]
    assert result.intent_type == IntentType.CREATE_EVENT
    assert result.raw_text == INCIDENT


def test_plain_trip_date_stays_important_date():
    result = parse("11月28日 親子旅行", now=NOW)
    assert result.intent_type == IntentType.ADD_IMPORTANT_DATE
    assert result.title == "親子旅行"
    assert result.notes == "yearly"


def test_hhmm_range_is_a_clock():
    text = "11月28日 0900-1500 親子旅行"
    assert _extract_time(text) == (9, 0)
    assert parse_control(text, now=NOW) is None
    result = parse(text, now=NOW)
    assert result.intent_type != IntentType.ADD_IMPORTANT_DATE
    assert result.start == datetime(2026, 11, 28, 9, 0, tzinfo=TZ)


@pytest.mark.parametrize(("text", "clock"), [
    ("0900-1500", (9, 0)), ("0900 - 1500", (9, 0)), ("0930–1100", (9, 30)),
    ("1400至1600", (14, 0)), ("0000到2359", (0, 0)), ("游水0800-0900", (8, 0)),
])
def test_hhmm_range_variants(text, clock):
    assert _extract_time(text) == clock


@pytest.mark.parametrize("text", [
    "0960-1500", "2500-2600", "0900-0960", "1500-0900", "09001-1500",
    "電話 9123-4567", "12345-6789", "a0900-1500",
])
def test_invalid_hhmm_range_is_not_a_clock(text):
    assert _extract_time(text) is None


def test_create_cue_with_exam_takes_create_path():
    text = "加event 12月3日 考試"
    assert parse_control(text, now=NOW) is None
    assert parse(text, now=NOW).intent_type != IntentType.ADD_IMPORTANT_DATE


@pytest.mark.parametrize("text", [
    "加event", "加個event", "加 個 Event", "add event", "Add Calendar Event",
    "ADD EVENT", "加入日曆", "加入 日曆", "幫我加個event", "唔好理佢，加event",
])
def test_create_cue_positive(text):
    assert has_create_cue(text) is True


@pytest.mark.parametrize("text", [
    "唔好加event", "唔使加個 event", "不要加入日曆", "don't add event",
    "no add event", "加生日", "加個重要日子", "",
])
def test_create_cue_negative(text):
    assert has_create_cue(text) is False


def test_negated_cue_keeps_important_date_and_clean_title():
    result = parse("唔好加event 11月28日 親子旅行", now=NOW)
    assert result.intent_type == IntentType.ADD_IMPORTANT_DATE
    assert result.title == "親子旅行"


def test_phone_number_without_date_unchanged():
    plain = "電話 9123 4567"
    linked = "電話 <tel:91234567|9123 4567>"
    assert normalize_slack_markup(linked) == plain
    assert _extract_time(plain) is None
    assert parse(linked, now=NOW).intent_type == parse(plain, now=NOW).intent_type


@pytest.mark.parametrize(("raw", "expected"), [
    ("<tel:0900-1500|0900 - 1500>", "0900 - 1500"),
    ("<tel:91234567>", "91234567"),
    ("<mailto:a@example.com|a@example.com> 收", "a@example.com 收"),
    ("<@U123> 加event", "<@U123> 加event"),
    ("<https://example.com|link> <#C1|plans>", "<https://example.com|link> <#C1|plans>"),
])
def test_normalize_slack_markup(raw, expected):
    assert normalize_slack_markup(raw) == expected
