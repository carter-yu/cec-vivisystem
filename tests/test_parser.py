"""Phase 1 unit tests for the offline natural-language parser."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from cec_vivisystem.models import Confidence, IntentType, ParseResult
from cec_vivisystem.parser import format_allowed_inputs, parse

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


# --- Phase 3 locked fixtures (phases/phase-3-parser-expansion.md) ---
L1 = "聽日上晝11點，帶Cedric去銅鑼灣上Miss Wong 堂"
L2 = "聽日下午3點帶 Cedric 去游泳"
L3 = "下晝2點 Elaine 睇牙醫"
L4 = "明天上晝10點 pediatrician for Cedric"


def test_parse_create_event_ting_yat_morning_class_causeway() -> None:
    """L1: live-style 聽日 + 上晝 + class/location phrase → create_event."""
    result = parse(L1, now=FIXED_NOW)
    _assert_create_event_common(result, L1)
    assert result.start == datetime(2026, 8, 9, 11, 0, tzinfo=FAMILY_TZ)
    assert "Cedric" in result.participants
    assert result.title is not None
    title_l = result.title.lower()
    assert (
        "miss wong" in title_l
        or "堂" in result.title
        or "lesson" in title_l
        or "class" in title_l
    )
    if result.location:
        assert "銅鑼灣" in result.location


def test_parse_create_event_ting_yat_afternoon_swim() -> None:
    """L2: 聽日 + 下午 swim → create_event."""
    result = parse(L2, now=FIXED_NOW)
    _assert_create_event_common(result, L2)
    assert result.start == datetime(2026, 8, 9, 15, 0, tzinfo=FAMILY_TZ)
    assert result.title is not None
    assert "游" in result.title or "swim" in result.title.lower()
    assert "Cedric" in result.participants


def test_parse_needs_clarification_period_time_without_date() -> None:
    """L3: 下晝+time without a day → needs_clarification; no invented date."""
    result = parse(L3, now=FIXED_NOW)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert result.raw_text == L3
    assert any("start" in f or "time" in f or "date" in f for f in result.missing_fields)
    assert result.start is None


def test_parse_create_event_mixed_tomorrow_morning_pediatrician() -> None:
    """L4: 明天 + 上晝 + English pediatrician → create_event."""
    result = parse(L4, now=FIXED_NOW)
    _assert_create_event_common(result, L4)
    assert result.start == datetime(2026, 8, 9, 10, 0, tzinfo=FAMILY_TZ)
    assert result.title is not None
    assert "pediatrician" in result.title.lower()
    assert "Cedric" in result.participants


def test_parse_logs_boundary_live_fixture() -> None:
    """L6: parse L1 completes with logging configured."""
    result = parse(L1, now=FIXED_NOW)
    assert result.intent_type == IntentType.CREATE_EVENT


def test_parse_list_events_english_sept() -> None:
    """Q1: English day query → list_events for 2026-09-01 HKT."""
    result = parse("tell me the events on 1 Sept 2026", now=FIXED_NOW)
    assert result.intent_type == IntentType.LIST_EVENTS
    assert result.all_day is True
    assert result.start == datetime(2026, 9, 1, 0, 0, tzinfo=FAMILY_TZ)
    assert result.end == datetime(2026, 9, 2, 0, 0, tzinfo=FAMILY_TZ)


def test_parse_list_events_cantonese_ymd() -> None:
    """Q2: 2026年9月1日有乜 → same day range."""
    result = parse("2026年9月1日有乜", now=FIXED_NOW)
    assert result.intent_type == IntentType.LIST_EVENTS
    assert result.start == datetime(2026, 9, 1, 0, 0, tzinfo=FAMILY_TZ)
    assert result.end == datetime(2026, 9, 2, 0, 0, tzinfo=FAMILY_TZ)


def test_parse_list_events_ting_yat() -> None:
    """Q3: 聽日有乜 at FIXED_NOW → 2026-08-09."""
    result = parse("聽日有乜", now=FIXED_NOW)
    assert result.intent_type == IntentType.LIST_EVENTS
    assert result.start == datetime(2026, 8, 9, 0, 0, tzinfo=FAMILY_TZ)
    assert result.end == datetime(2026, 8, 10, 0, 0, tzinfo=FAMILY_TZ)


def test_parse_list_without_date_needs_clarification() -> None:
    """Q6: 有乜 with no date → needs_clarification missing start."""
    result = parse("有乜", now=FIXED_NOW)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert "start" in result.missing_fields


# --- Phase 9 locked fixtures (phases/phase-9-parser-aliases.md) ---
A1 = "聽日9點，梓梵游水"
A3 = "聽日上晝11點，帶梓梵去銅鑼灣上MS Wong 堂"
A6 = "梓梵同朋友一齊砌積木，好開心。"


def test_parse_ting_yat_zifan_yau_seui() -> None:
    """A1: live 聽日9點，梓梵游水 → create_event 游泳 + Cedric."""
    result = parse(A1, now=FIXED_NOW)
    _assert_create_event_common(result, A1)
    assert result.title == "游泳"
    assert result.start == datetime(2026, 8, 9, 9, 0, tzinfo=FAMILY_TZ)
    assert "Cedric" in result.participants


def test_parse_ms_wong_with_zifan() -> None:
    """A3: MS Wong 堂 + 梓梵 → Miss Wong 堂 + Cedric."""
    result = parse(A3, now=FIXED_NOW)
    _assert_create_event_common(result, A3)
    assert result.start == datetime(2026, 8, 9, 11, 0, tzinfo=FAMILY_TZ)
    assert "Cedric" in result.participants
    assert result.title is not None
    title_l = result.title.lower()
    assert "miss wong" in title_l or "堂" in result.title
    if result.location:
        assert "銅鑼灣" in result.location


def test_parse_zifan_life_note_is_not_create() -> None:
    """A6: 梓梵 life-note text is not a calendar create."""
    result = parse(A6, now=FIXED_NOW)
    assert result.intent_type == IntentType.UNKNOWN
    assert result.raw_text == A6
    assert result.start is None


# --- Phase 10 locked fixtures (phases/phase-10-parser-titles.md) ---
T1 = "聽日下午3點去公園"
T2 = "Sunday 10am playgroup"
T3 = "聽日上午9點游水班"
T4 = "星期六下午2點體能班"
T5 = "聽日上晝11點手作工作坊"
T6 = "聽日下午4點去商場"
T7 = "Sunday 3pm birthday party"
T8 = "聽日上午10點打針"
T9 = "公園啲花好靚。"


def test_parse_ting_yat_park() -> None:
    """T1: 聽日下午3點去公園 → create_event 公園."""
    result = parse(T1, now=FIXED_NOW)
    _assert_create_event_common(result, T1)
    assert result.title == "公園"
    assert result.start == datetime(2026, 8, 9, 15, 0, tzinfo=FAMILY_TZ)


def test_parse_sunday_playgroup() -> None:
    """T2: Sunday 10am playgroup → create_event playgroup."""
    result = parse(T2, now=FIXED_NOW)
    _assert_create_event_common(result, T2)
    assert result.title == "playgroup"
    assert result.start == datetime(2026, 8, 9, 10, 0, tzinfo=FAMILY_TZ)


def test_parse_swim_class() -> None:
    """T3: 游水班 keeps canonical title 游泳."""
    result = parse(T3, now=FIXED_NOW)
    _assert_create_event_common(result, T3)
    assert result.title == "游泳"
    assert result.start == datetime(2026, 8, 9, 9, 0, tzinfo=FAMILY_TZ)


def test_parse_gymnastics_class() -> None:
    """T4: 星期六下午2點體能班 → 體能班 same Saturday."""
    result = parse(T4, now=FIXED_NOW)
    _assert_create_event_common(result, T4)
    assert result.title == "體能班"
    assert result.start == datetime(2026, 8, 8, 14, 0, tzinfo=FAMILY_TZ)


def test_parse_workshop() -> None:
    """T5: 手作工作坊 → title 手作."""
    result = parse(T5, now=FIXED_NOW)
    _assert_create_event_common(result, T5)
    assert result.title == "手作"
    assert result.start == datetime(2026, 8, 9, 11, 0, tzinfo=FAMILY_TZ)


def test_parse_mall() -> None:
    """T6: 聽日下午4點去商場 → 商場."""
    result = parse(T6, now=FIXED_NOW)
    _assert_create_event_common(result, T6)
    assert result.title == "商場"
    assert result.start == datetime(2026, 8, 9, 16, 0, tzinfo=FAMILY_TZ)


def test_parse_birthday_party() -> None:
    """T7: Sunday 3pm birthday party → 生日會."""
    result = parse(T7, now=FIXED_NOW)
    _assert_create_event_common(result, T7)
    assert result.title == "生日會"
    assert result.start == datetime(2026, 8, 9, 15, 0, tzinfo=FAMILY_TZ)


def test_parse_vaccine() -> None:
    """T8: 聽日上午10點打針 → 打針."""
    result = parse(T8, now=FIXED_NOW)
    _assert_create_event_common(result, T8)
    assert result.title == "打針"
    assert result.start == datetime(2026, 8, 9, 10, 0, tzinfo=FAMILY_TZ)


def test_parse_park_chat_is_not_create() -> None:
    """T9: park mention without a schedule is not a calendar create."""
    result = parse(T9, now=FIXED_NOW)
    assert result.intent_type == IntentType.UNKNOWN
    assert result.raw_text == T9
    assert result.start is None


# --- Phase 11 locked fixtures (phases/phase-11-ting-chiu.md) ---
M1 = "聽朝11點帶梓梵去MS Wong 度上堂"
M3 = "聽朝帶梓梵去MS Wong 堂"


def test_parse_ting_chiu_ms_wong_zifan() -> None:
    """M1: live 聽朝11點 + 梓梵 + MS Wong → tomorrow 11:00, Cedric."""
    result = parse(M1, now=FIXED_NOW)
    _assert_create_event_common(result, M1)
    assert result.title is not None
    title_l = result.title.lower()
    assert "miss wong" in title_l or "堂" in result.title
    assert result.start == datetime(2026, 8, 9, 11, 0, tzinfo=FAMILY_TZ)
    assert "Cedric" in result.participants


def test_parse_ting_chiu_missing_clock() -> None:
    """M3: 聽朝 without a clock → needs_clarification; no invented time."""
    result = parse(M3, now=FIXED_NOW)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert result.raw_text == M3
    assert any("start" in f or "time" in f or "date" in f for f in result.missing_fields)
    assert result.start is None
    assert "Cedric" in result.participants


# --- Phase 12 locked fixtures (phases/phase-12-morning-recap.md) ---
PHASE12_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=FAMILY_TZ)
P0 = "聽日有乜嘢活動"
P0B_YE = "聽日有乜嘢"
P0B_WHAT = "聽日有什麼活動"


def _assert_list_tomorrow_hkt(result: ParseResult, raw: str, now: datetime) -> None:
    assert result.intent_type == IntentType.LIST_EVENTS
    assert result.raw_text == raw
    assert result.intent_type != IntentType.CREATE_EVENT
    assert result.intent_type != IntentType.UNKNOWN
    start = datetime.combine(
        now.astimezone(FAMILY_TZ).date() + timedelta(days=1),
        datetime.min.time(),
        tzinfo=FAMILY_TZ,
    )
    assert result.start == start
    assert result.end == start + timedelta(days=1)
    assert result.all_day is True


def test_parse_list_events_ting_yat_ye_activity() -> None:
    """P0: 聽日有乜嘢活動 → list_events for tomorrow, not create/unknown."""
    result = parse(P0, now=PHASE12_NOW)
    _assert_list_tomorrow_hkt(result, P0, PHASE12_NOW)


def test_parse_list_events_ting_yat_ye() -> None:
    """P0b: 聽日有乜嘢 → same tomorrow list window."""
    result = parse(P0B_YE, now=PHASE12_NOW)
    _assert_list_tomorrow_hkt(result, P0B_YE, PHASE12_NOW)


def test_parse_list_events_ting_yat_what_activity() -> None:
    """P0b: 聽日有什麼活動 → same tomorrow list window."""
    result = parse(P0B_WHAT, now=PHASE12_NOW)
    _assert_list_tomorrow_hkt(result, P0B_WHAT, PHASE12_NOW)


def test_parse_help_whole_message() -> None:
    """help / 指令 / 點用 as the whole message → help, not create/list."""
    for text in ("help", "/help", "HELP?", "指令", "點用", "有咩指令"):
        result = parse(text, now=FIXED_NOW)
        assert result.intent_type == IntentType.HELP, text
        assert result.raw_text == text
        assert result.start is None
        assert result.intent_type != IntentType.CREATE_EVENT
        assert result.intent_type != IntentType.LIST_EVENTS


def test_parse_help_does_not_steal_create_or_list() -> None:
    """Embedded help / holiday / 聽日有乜 stay on their own intents."""
    holiday = parse("明天全日 Cedric 學校 holiday", now=FIXED_NOW)
    assert holiday.intent_type == IntentType.CREATE_EVENT
    listed = parse("聽日有乜", now=FIXED_NOW)
    assert listed.intent_type == IntentType.LIST_EVENTS
    book = parse("help me book 游泳", now=FIXED_NOW)
    assert book.intent_type != IntentType.HELP


def test_format_allowed_inputs_lists_common_phrases() -> None:
    text = format_allowed_inputs()
    assert "聽日有乜" in text
    assert "今日有乜" in text
    assert "今個星期有乜" in text
    assert "今個月有乜" in text
    assert "聽朝" in text
    assert "梓梵" in text
    assert "help" in text.lower()
    assert "指令" in text
    assert "No calendar change was made" in text


# --- Phase 14 locked fixtures (phases/phase-14-period-recap.md) ---
PHASE14_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=FAMILY_TZ)  # Tuesday
P1_TODAY = "今日有乜？"
P2_WEEK = "今個星期有乜"
P2_WEEK_LIBAI = "今個禮拜有乜"
P3_NEXT_WEEK = "下個星期有乜"
P4_MONTH = "今個月有乜"
P5_RANGE = "9月1日至9月7日有乜"


def _assert_list_window(
    result: ParseResult,
    raw: str,
    start: datetime,
    end: datetime,
) -> None:
    assert result.intent_type == IntentType.LIST_EVENTS
    assert result.raw_text == raw
    assert result.intent_type != IntentType.CREATE_EVENT
    assert result.intent_type != IntentType.UNKNOWN
    assert result.intent_type != IntentType.NEEDS_CLARIFICATION
    assert result.start == start
    assert result.end == end
    assert result.all_day is True
    assert result.missing_fields == []


def test_parse_list_events_today() -> None:
    """P1: 今日有乜？ → list_events for today, not create/clarification."""
    result = parse(P1_TODAY, now=PHASE14_NOW)
    _assert_list_window(
        result,
        P1_TODAY,
        datetime(2026, 9, 8, 0, 0, tzinfo=FAMILY_TZ),
        datetime(2026, 9, 9, 0, 0, tzinfo=FAMILY_TZ),
    )
    bare = parse("今日有乜", now=PHASE14_NOW)
    _assert_list_window(
        bare,
        "今日有乜",
        datetime(2026, 9, 8, 0, 0, tzinfo=FAMILY_TZ),
        datetime(2026, 9, 9, 0, 0, tzinfo=FAMILY_TZ),
    )


def test_parse_list_events_this_week() -> None:
    """P2: 今個星期 / 今個禮拜 → this Monday to next Monday HKT."""
    start = datetime(2026, 9, 7, 0, 0, tzinfo=FAMILY_TZ)
    end = datetime(2026, 9, 14, 0, 0, tzinfo=FAMILY_TZ)
    for text in (P2_WEEK, P2_WEEK_LIBAI):
        result = parse(text, now=PHASE14_NOW)
        _assert_list_window(result, text, start, end)


def test_parse_list_events_next_week() -> None:
    """P3: 下個星期有乜 → next Monday to the Monday after."""
    result = parse(P3_NEXT_WEEK, now=PHASE14_NOW)
    _assert_list_window(
        result,
        P3_NEXT_WEEK,
        datetime(2026, 9, 14, 0, 0, tzinfo=FAMILY_TZ),
        datetime(2026, 9, 21, 0, 0, tzinfo=FAMILY_TZ),
    )


def test_parse_list_events_this_month() -> None:
    """P4: 今個月有乜 → 1st of month to 1st of next month."""
    result = parse(P4_MONTH, now=PHASE14_NOW)
    _assert_list_window(
        result,
        P4_MONTH,
        datetime(2026, 9, 1, 0, 0, tzinfo=FAMILY_TZ),
        datetime(2026, 10, 1, 0, 0, tzinfo=FAMILY_TZ),
    )


def test_parse_list_events_date_range() -> None:
    """P5: 9月1日至9月7日有乜 → inclusive days, exclusive end 9月8日."""
    result = parse(P5_RANGE, now=PHASE14_NOW)
    _assert_list_window(
        result,
        P5_RANGE,
        datetime(2026, 9, 1, 0, 0, tzinfo=FAMILY_TZ),
        datetime(2026, 9, 8, 0, 0, tzinfo=FAMILY_TZ),
    )


def test_parse_list_events_next_weekday_is_not_next_week() -> None:
    """下星期三有乜 stays a day list, not 下個星期."""
    result = parse("下星期三有乜", now=PHASE14_NOW)
    _assert_list_window(
        result,
        "下星期三有乜",
        datetime(2026, 9, 9, 0, 0, tzinfo=FAMILY_TZ),
        datetime(2026, 9, 10, 0, 0, tzinfo=FAMILY_TZ),
    )


def test_parse_today_weather_still_unknown() -> None:
    """Q5: 今日天氣點呀 stays unknown (not a today list)."""
    result = parse("今日天氣點呀", now=PHASE14_NOW)
    assert result.intent_type == IntentType.UNKNOWN
