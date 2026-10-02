"""Phase 27 weekday-series create (S0–S11). Fake model/calendar; fixed HKT clock."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_writer import (
    FakeCalendarClient,
    InMemoryCalendarAuditStore,
    _draft_to_google_event,
    series_child_key,
    write_calendar_create,
    write_calendar_series_create,
)
from cec_vivisystem.confirmation import (
    InMemoryConfirmationStore,
    JsonDirConfirmationStore,
    build_proposal,
    create_confirmation,
    resolve_confirmation,
)
from cec_vivisystem.listener import format_reply, process_slack_message_event
from cec_vivisystem.models import (
    CalendarListedEvent,
    CalendarWriteOutcome,
    ConfirmationStatus,
    IntentType,
    OverlapCheckOutcome,
    ParseResult,
    SeriesSpec,
)
from cec_vivisystem.overlap import detect_series_overlaps
from cec_vivisystem.parse_fallback import (
    FakeLlmParser,
    XaiChatLlmParser,
    llm_payload_to_parse_result,
    parse_with_fallback,
    validate_create_payload,
)
from cec_vivisystem.parser import parse
from cec_vivisystem.series import (
    MAX_OCCURRENCES,
    SeriesExpansionError,
    apply_series_policy,
    expand_weekday_series,
    extract_series_from_text,
    has_series_cue,
)

TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=TZ)  # Friday
CHANNEL = "C_PLANS"
SERIES_TEXT = "逢星期一至五 8:30-12:00，10月2日至10月30日 暑期班"
MON_FRI = (0, 1, 2, 3, 4)
OCT_SPEC = SeriesSpec(
    weekdays=MON_FRI,
    range_start=date(2026, 10, 2),
    range_end=date(2026, 10, 30),
    start_time=time(8, 30),
    end_time=time(12, 0),
)


def _create(text: str = SERIES_TEXT, **overrides) -> ParseResult:
    fields = {
        "intent_type": IntentType.CREATE_EVENT,
        "title": "暑期班",
        "start": datetime(2026, 10, 2, 8, 30, tzinfo=TZ),
        "end": datetime(2026, 10, 2, 12, 0, tzinfo=TZ),
        "all_day": False,
        "location": None,
        "participants": ["Cedric"],
        "raw_text": text,
        "notes": "llm_first",
    }
    fields.update(overrides)
    return ParseResult(**fields)


def _event(text: str, ts: str = "1791000000.000100", **extra) -> dict:
    return {
        "type": "message",
        "channel": CHANNEL,
        "user": "U_PARENT",
        "text": text,
        "ts": ts,
        "client_msg_id": f"msg-{ts}",
        **extra,
    }


def _kwargs(store, calendar, audit, llm=None) -> dict:
    return {
        "allowed_channel_ids": {CHANNEL},
        "confirmation_store": store,
        "calendar_client": calendar,
        "calendar_audit_store": audit,
        "llm_parser": llm,
        "now": NOW,
    }


def _yes(ts: str = "1791000000.000100", reply_ts: str = "1791000001.000100") -> dict:
    return _event("yes", ts=reply_ts, thread_ts=ts)


def _accepted_series(store=None):
    store = store or InMemoryConfirmationStore()
    pending = create_confirmation(
        _create(series=OCT_SPEC), store=store, now=NOW, channel_id=CHANNEL,
        thread_ts="t1", source_message_id="t1",
    )
    return resolve_confirmation(pending.confirmation_id, "accept", store=store, now=NOW)


# --- S0 span guard -------------------------------------------------------------


def test_s0_timed_multi_day_single_create_becomes_clarification():
    spanning = _create(
        "下星期一至下下星期五 早上 暑期班",  # no explicit weekday-set cue
        start=datetime(2026, 10, 5, 8, 30, tzinfo=TZ),
        end=datetime(2026, 10, 30, 12, 0, tzinfo=TZ),
    )
    guarded = apply_series_policy(spanning, now=NOW)
    assert guarded.intent_type == IntentType.NEEDS_CLARIFICATION
    assert guarded.notes == "span_guard"
    assert guarded.series is None
    reply = format_reply(guarded)
    assert "跨咗幾日" in reply and "No calendar change was made" in reply


def test_s0_listener_never_proposes_spanning_timed_create():
    llm = FakeLlmParser(
        _create(
            "暑期班 10月5日早上8:30開始，10月30日中午12點完",
            start=datetime(2026, 10, 5, 8, 30, tzinfo=TZ),
            end=datetime(2026, 10, 30, 12, 0, tzinfo=TZ),
        )
    )
    store, calendar = InMemoryConfirmationStore(), FakeCalendarClient()
    result = process_slack_message_event(
        _event("暑期班 10月5日早上8:30開始，10月30日中午12點完"),
        **_kwargs(store, calendar, InMemoryCalendarAuditStore(), llm),
    )
    assert result.parse_result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert store.list_all() == []
    assert calendar.calls == [] and calendar.list_calls == []


def test_s0_writer_refuses_legacy_spanning_confirmation_without_google_call():
    store = InMemoryConfirmationStore()
    spanning = _create(end=datetime(2026, 10, 30, 12, 0, tzinfo=TZ), raw_text="legacy")
    pending = create_confirmation(spanning, store=store, now=NOW)
    accepted = resolve_confirmation(pending.confirmation_id, "accept", store=store, now=NOW)
    calendar = FakeCalendarClient()
    result = write_calendar_create(accepted, client=calendar)
    assert result.outcome == CalendarWriteOutcome.REFUSED
    assert result.error_type == "multi_day_timed_span"
    assert calendar.calls == []


def test_same_day_timed_create_is_not_guarded():
    single = _create("聽日 8:30 暑期班", end=datetime(2026, 10, 2, 12, 0, tzinfo=TZ))
    assert apply_series_policy(single, now=NOW) == single


# --- S1 expand ------------------------------------------------------------------


def test_s1_mon_fri_oct_expands_to_21_mornings():
    occurrences = expand_weekday_series(OCT_SPEC)
    assert len(occurrences) == 21
    assert occurrences[0].start == datetime(2026, 10, 2, 8, 30, tzinfo=TZ)
    assert occurrences[-1].end == datetime(2026, 10, 30, 12, 0, tzinfo=TZ)
    for occurrence in occurrences:
        assert occurrence.start.date() == occurrence.end.date() == occurrence.local_date
        assert occurrence.local_date.weekday() < 5
        assert (occurrence.start.time(), occurrence.end.time()) == (time(8, 30), time(12, 0))


@pytest.mark.parametrize(
    "text",
    [
        SERIES_TEXT,
        "逢星期一至五 8:30–12:00 from Oct 2–30 暑期班",
        "Mon-Fri 8:30am-12pm Oct 2 to Oct 30 summer class",
    ],
)
def test_s1_text_series_matches_spec(text):
    spec = extract_series_from_text(text, now=NOW)
    assert spec is not None
    assert len(expand_weekday_series(spec)) == 21


def test_s1_open_window_defaults_one_hour():
    spec = SeriesSpec(weekdays=(0,), range_start=date(2026, 10, 5),
                      range_end=date(2026, 10, 5), start_time=time(9, 0))
    (only,) = expand_weekday_series(spec)
    assert only.end == datetime(2026, 10, 5, 10, 0, tzinfo=TZ)


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (SeriesSpec((), date(2026, 10, 5), date(2026, 10, 9), time(9)), "empty_weekdays"),
        (SeriesSpec((0,), date(2026, 10, 9), date(2026, 10, 5), time(9)), "invalid_range"),
        (SeriesSpec((0,), date(2026, 10, 5), date(2026, 10, 9), time(22), time(1)),
         "invalid_window"),
        (SeriesSpec((5,), date(2026, 10, 5), date(2026, 10, 9), time(9)), "no_occurrences"),
    ],
)
def test_expand_refuses_invalid_specs(spec, reason):
    with pytest.raises(SeriesExpansionError) as info:
        expand_weekday_series(spec)
    assert info.value.reason == reason


# --- S2 proposal ----------------------------------------------------------------


def test_s2_one_proposal_lists_count_sample_and_overflow():
    store = InMemoryConfirmationStore()
    calendar = FakeCalendarClient()
    llm = FakeLlmParser(_create(series=OCT_SPEC))
    result = process_slack_message_event(
        _event(SERIES_TEXT), **_kwargs(store, calendar, InMemoryCalendarAuditStore(), llm)
    )
    assert len(store.list_all()) == 1
    text = result.reply_text
    assert "共 21 次" in text and "21 occurrences" in text
    assert "逢星期一至五" in text and "08:30–12:00" in text
    assert "2026-10-02（五）08:30–12:00" in text
    assert "…另外 16 次" in text and "2026-10-30（五）" in text
    assert "No calendar change will be made until you confirm." in text
    assert "created" not in text.lower()
    assert calendar.calls == []


def test_s2_series_round_trips_through_json_store(tmp_path):
    store = JsonDirConfirmationStore(tmp_path)
    accepted = _accepted_series(store)
    loaded = store.get(accepted.confirmation_id)
    assert loaded.parse_result.series == OCT_SPEC
    assert loaded.status == ConfirmationStatus.ACCEPTED


# --- S3 per-occurrence overlap --------------------------------------------------


def _listed(event_id, start, end, participants=None):
    return CalendarListedEvent(event_id=event_id, summary=event_id, start=start, end=end,
                               all_day=False, participants=participants or [])


def test_s3_afternoon_event_mid_range_does_not_warn_every_morning():
    afternoon = _listed("piano", datetime(2026, 10, 14, 15, 0, tzinfo=TZ),
                        datetime(2026, 10, 14, 16, 0, tzinfo=TZ))
    morning = _listed("dentist", datetime(2026, 10, 20, 9, 0, tzinfo=TZ),
                      datetime(2026, 10, 20, 10, 0, tzinfo=TZ), ["Cedric"])
    calendar = FakeCalendarClient(listed_events=[afternoon, morning])
    result = detect_series_overlaps(
        expand_weekday_series(OCT_SPEC), participants=["Cedric"], client=calendar
    )
    assert result.outcome == OverlapCheckOutcome.SUCCESS
    assert [(h.occurrence.local_date, h.hit.event.event_id) for h in result.hits] == [
        (date(2026, 10, 20), "dentist")
    ]
    assert result.hits[0].hit.same_person_names == ["Cedric"]
    assert len(calendar.list_calls) == 1


def test_s3_proposal_shows_only_true_morning_hits():
    afternoon = _listed("piano", datetime(2026, 10, 14, 15, 0, tzinfo=TZ),
                        datetime(2026, 10, 14, 16, 0, tzinfo=TZ))
    morning = _listed("dentist", datetime(2026, 10, 20, 9, 0, tzinfo=TZ),
                      datetime(2026, 10, 20, 10, 0, tzinfo=TZ))
    calendar = FakeCalendarClient(listed_events=[afternoon, morning])
    result = process_slack_message_event(
        _event(SERIES_TEXT),
        **_kwargs(InMemoryConfirmationStore(), calendar, InMemoryCalendarAuditStore(),
                  FakeLlmParser(_create(series=OCT_SPEC))),
    )
    assert "21 次之中有 1 次撞期" in result.reply_text
    assert "2026-10-20 ↔ 09:00–10:00 dentist" in result.reply_text
    assert "piano" not in result.reply_text


def test_s3_list_failure_warns_but_still_proposes():
    calendar = FakeCalendarClient(fail_list_with=RuntimeError("synthetic"))
    store = InMemoryConfirmationStore()
    result = process_slack_message_event(
        _event(SERIES_TEXT),
        **_kwargs(store, calendar, InMemoryCalendarAuditStore(),
                  FakeLlmParser(_create(series=OCT_SPEC))),
    )
    assert "未能檢查撞期" in result.reply_text
    assert len(store.list_pending()) == 1


# --- S4 cap ---------------------------------------------------------------------


def test_s4_over_cap_clarifies_without_truncation():
    long_spec = SeriesSpec(MON_FRI, date(2026, 10, 1), date(2026, 12, 31), time(8, 30),
                           time(12))
    with pytest.raises(SeriesExpansionError) as info:
        expand_weekday_series(long_spec)
    assert info.value.reason == "over_cap" and info.value.count > MAX_OCCURRENCES
    store = InMemoryConfirmationStore()
    result = process_slack_message_event(
        _event(SERIES_TEXT),
        **_kwargs(store, FakeCalendarClient(), InMemoryCalendarAuditStore(),
                  FakeLlmParser(_create(series=long_spec))),
    )
    assert result.parse_result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert f"有 {info.value.count} 次" in result.reply_text
    assert f"{MAX_OCCURRENCES}" in result.reply_text
    assert store.list_all() == []


def test_open_ended_series_clarifies():
    text = "逢星期三 4點至5點 游水"
    guarded = apply_series_policy(_create(text, title="游泳"), now=NOW)
    assert guarded.intent_type == IntentType.NEEDS_CLARIFICATION
    assert guarded.notes == "series_open_ended"
    assert "結束日期" in format_reply(guarded)


# --- S5 / S6 / S7 writer ---------------------------------------------------------


def test_s5_yes_writes_each_child_with_stable_ids_and_audit():
    store, audit = InMemoryConfirmationStore(), InMemoryCalendarAuditStore()
    calendar = FakeCalendarClient()
    kwargs = _kwargs(store, calendar, audit, FakeLlmParser(_create(series=OCT_SPEC)))
    process_slack_message_event(_event(SERIES_TEXT), **kwargs)
    result = process_slack_message_event(_yes(), **kwargs)
    assert result.reply_text.startswith("已確認。已建立 21 個活動")
    assert len(calendar.calls) == 21
    (conf,) = store.list_all()
    expected = [series_child_key(conf.confirmation_id, o.local_date)
                for o in expand_weekday_series(OCT_SPEC)]
    assert [d.confirmation_id for d in calendar.calls] == expected
    body = _draft_to_google_event(calendar.calls[0])
    assert body["id"] == hashlib.sha256(
        ("cec-confirmation:" + conf.confirmation_id + "#2026-10-02").encode()
    ).hexdigest()
    assert body["extendedProperties"]["private"] == {
        "confirmation_id": f"{conf.confirmation_id}#2026-10-02",
        "parent_confirmation_id": conf.confirmation_id,
        "occurrence_date": "2026-10-02",
        "series_id": conf.confirmation_id,
    }
    assert body["start"]["dateTime"] == "2026-10-02T08:30:00+08:00"
    assert body["end"]["dateTime"] == "2026-10-02T12:00:00+08:00"
    rows = audit.list_all()
    assert len(rows) == 21
    assert {r.op for r in rows} == {"create"}
    assert {r.confirmation_id for r in rows} == set(expected)


def test_s6_second_yes_only_writes_missing_children():
    audit = InMemoryCalendarAuditStore()
    accepted = _accepted_series()
    first = write_calendar_series_create(
        accepted, client=FakeCalendarClient(fail_calls={3, 4}), audit_store=audit
    )
    assert (first.created_count, first.failed_count) == (19, 2)
    retry_calendar = FakeCalendarClient()
    second = write_calendar_series_create(accepted, client=retry_calendar, audit_store=audit)
    assert len(retry_calendar.calls) == 2
    assert {d.occurrence_date for d in retry_calendar.calls} == {
        date(2026, 10, 6), date(2026, 10, 7)
    }
    assert (second.created_count, second.already_count, second.failed_count) == (2, 19, 0)
    assert second.outcome == CalendarWriteOutcome.SUCCESS
    third_calendar = FakeCalendarClient()
    third = write_calendar_series_create(accepted, client=third_calendar, audit_store=audit)
    assert third.outcome == CalendarWriteOutcome.ALREADY_CREATED
    assert third_calendar.calls == []


def test_s6_listener_redelivered_yes_reports_already_added():
    store, audit = InMemoryConfirmationStore(), InMemoryCalendarAuditStore()
    calendar = FakeCalendarClient()
    kwargs = _kwargs(store, calendar, audit, FakeLlmParser(_create(series=OCT_SPEC)))
    process_slack_message_event(_event(SERIES_TEXT), **kwargs)
    process_slack_message_event(_yes(), **kwargs)
    again = process_slack_message_event(_yes(reply_ts="1791000002.000100"), **kwargs)
    assert "全部 21 個活動之前已經建立" in again.reply_text
    assert len(calendar.calls) == 21


def test_s7_partial_failure_reports_honest_counts():
    store, audit = InMemoryConfirmationStore(), InMemoryCalendarAuditStore()
    calendar = FakeCalendarClient(fail_calls={5})
    kwargs = _kwargs(store, calendar, audit, FakeLlmParser(_create(series=OCT_SPEC)))
    process_slack_message_event(_event(SERIES_TEXT), **kwargs)
    result = process_slack_message_event(_yes(), **kwargs)
    assert len(calendar.calls) == 21  # loop continued past the failure
    assert "1/21 個活動未能建立" in result.reply_text
    assert "新建立 20 個" in result.reply_text
    assert "已建立 21" not in result.reply_text
    failed = [r for r in audit.list_all() if r.outcome == CalendarWriteOutcome.FAILED]
    assert len(failed) == 1


def test_series_writer_refuses_pending_and_missing_id():
    store = InMemoryConfirmationStore()
    pending = create_confirmation(_create(series=OCT_SPEC), store=store, now=NOW)
    calendar = FakeCalendarClient()
    refused = write_calendar_series_create(pending, client=calendar)
    assert refused.outcome == CalendarWriteOutcome.REFUSED
    assert refused.error_type == "not_accepted"
    accepted = _accepted_series()
    accepted.confirmation_id = " "
    assert write_calendar_series_create(accepted, client=calendar).error_type == (
        "missing_confirmation_id"
    )
    assert calendar.calls == []


def test_single_writer_refuses_series_confirmation():
    calendar = FakeCalendarClient()
    result = write_calendar_create(_accepted_series(), client=calendar)
    assert result.error_type == "series_requires_series_writer"
    assert calendar.calls == []


def test_series_write_logs_counts(monkeypatch):
    logger = Mock()
    monkeypatch.setattr("cec_vivisystem.calendar_writer.logger", logger)
    write_calendar_series_create(
        _accepted_series(), client=FakeCalendarClient(fail_calls={1}),
        audit_store=InMemoryCalendarAuditStore(),
    )
    (call,) = [c for c in logger.error.call_args_list if c.args == ("series_write_completed",)]
    assert call.kwargs["created_count"] == 20
    assert call.kwargs["failed_count"] == 1
    assert call.kwargs["already_count"] == 0
    assert call.kwargs["occurrence_count"] == 21
    assert "duration_ms" in call.kwargs and call.kwargs["confirmation_id"]


# --- S8 single-event path unchanged ---------------------------------------------


def test_s8_single_event_create_unchanged():
    message = "星期六下午3點帶 Cedric 去游泳"
    parsed = parse(message, now=NOW)
    assert not has_series_cue(message)
    assert apply_series_policy(parsed, now=NOW) == parsed
    proposal = build_proposal(parsed)
    assert proposal.startswith("Please confirm this calendar create proposal:")
    store, audit = InMemoryConfirmationStore(), InMemoryCalendarAuditStore()
    calendar = FakeCalendarClient()
    kwargs = _kwargs(store, calendar, audit)
    process_slack_message_event(_event(message), **kwargs)
    result = process_slack_message_event(_yes(), **kwargs)
    assert result.reply_text == "Accepted. Calendar event created."
    assert len(calendar.calls) == 1
    assert calendar.calls[0].series_id is None


# --- S9 weekday subset ------------------------------------------------------------


def test_s9_mon_wed_fri_expands_only_those_days():
    spec = extract_series_from_text("星期一、三、五 下晝2點至4點 10月5日至10月16日 游水",
                                    now=NOW)
    assert spec.weekdays == (0, 2, 4)
    occurrences = expand_weekday_series(spec)
    assert [o.local_date.day for o in occurrences] == [5, 7, 9, 12, 14, 16]
    assert {o.start.hour for o in occurrences} == {14}


def test_s9_offline_rules_mode_detects_text_series():
    store = InMemoryConfirmationStore()
    result = process_slack_message_event(
        _event("Mon/Wed/Fri 3pm-5pm 5 Oct to 16 Oct swim"),
        **_kwargs(store, FakeCalendarClient(), InMemoryCalendarAuditStore()),
    )
    assert result.parse_result.series.weekdays == (0, 2, 4)
    assert "共 6 次" in result.reply_text


# --- S10 model collapses series into a span ---------------------------------------


def test_s10_model_long_span_with_series_cue_is_expanded_not_inserted():
    collapsed = _create(
        "逢星期一至五 早上 8:30 至中午 暑期班，10月尾完",
        start=datetime(2026, 10, 5, 8, 30, tzinfo=TZ),
        end=datetime(2026, 10, 30, 12, 0, tzinfo=TZ),
    )
    fixed = apply_series_policy(collapsed, now=NOW)
    assert fixed.intent_type == IntentType.CREATE_EVENT
    assert fixed.series == SeriesSpec(MON_FRI, date(2026, 10, 5), date(2026, 10, 30),
                                      time(8, 30), time(12, 0))
    assert fixed.end.date() == fixed.start.date()
    assert len(expand_weekday_series(fixed.series)) == 20


def test_s10_model_single_first_day_with_full_series_text_uses_series():
    first_only = _create()  # model returned just Oct 2 for the series phrase
    fixed = apply_series_policy(first_only, now=NOW)
    assert fixed.series == OCT_SPEC


def _provider(monkeypatch, response):
    create = Mock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response)))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
    ))
    monkeypatch.setattr("openai.OpenAI", Mock(return_value=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))))
    return XaiChatLlmParser(api_key="fake-key", model="fake-model"), create


def _series_payload(**series):
    return {
        "intent_type": "create_event",
        "title": "暑期班",
        "start": "2026-10-02T08:30:00+08:00",
        "end": "2026-10-02T12:00:00+08:00",
        "all_day": False,
        "location": None,
        "participants": [],
        "missing_fields": [],
        "confidence": "medium",
        "series": {
            "weekdays": ["MO", "TU", "WE", "TH", "FR"],
            "range_start": "2026-10-02",
            "range_end": "2026-10-30",
            "start_time": "08:30",
            "end_time": "12:00",
        } | series,
    }


def test_s10_model_schema_series_maps_to_spec(monkeypatch):
    llm, create = _provider(monkeypatch, _series_payload())
    result = parse_with_fallback(SERIES_TEXT, now=NOW, llm=llm, llm_first=True)
    assert result.series == OCT_SPEC
    request = create.call_args.kwargs
    schema = request["response_format"]["json_schema"]["schema"]
    assert "series" in schema["required"]
    assert "Weekday series" in request["messages"][0]["content"]


def test_model_open_ended_series_clarifies(monkeypatch):
    llm, _ = _provider(monkeypatch, _series_payload(range_end=None))
    result = parse_with_fallback("逢星期一 9點 游水", now=NOW, llm=llm, llm_first=True)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert "結束日期" in format_reply(apply_series_policy(result, now=NOW))


@pytest.mark.parametrize(
    "series",
    [
        {"weekdays": []},
        {"weekdays": ["XX"]},
        {"range_start": "Oct 2"},
        {"start_time": 830},
        {"extra": 1},
    ],
)
def test_series_payload_validation_rejects_garbage(series):
    with pytest.raises((ValueError, TypeError)):
        validate_create_payload(_series_payload(**series))


def test_payload_without_series_key_stays_valid():
    payload = _series_payload()
    del payload["series"]
    validate_create_payload(payload)
    result = llm_payload_to_parse_result(payload, raw_text="x", now=NOW)
    assert result.series is None


# --- S11 all-day multi-day ---------------------------------------------------------


def test_s11_all_day_multi_day_clarifies_and_never_invents_series():
    trip = _create(
        "10月5日至10月9日 全日 去旅行",
        title="旅行",
        all_day=True,
        start=datetime(2026, 10, 5, tzinfo=TZ),
        end=datetime(2026, 10, 10, tzinfo=TZ),
    )
    guarded = apply_series_policy(trip, now=NOW)
    assert guarded.intent_type == IntentType.NEEDS_CLARIFICATION
    assert guarded.notes == "all_day_span"
    assert guarded.series is None
    assert "多日全日活動" in format_reply(guarded)


def test_single_all_day_still_proposes():
    day = _create("10月5日 全日 學校 holiday", title="學校 holiday", all_day=True,
                  start=datetime(2026, 10, 5, tzinfo=TZ), end=datetime(2026, 10, 6, tzinfo=TZ))
    assert apply_series_policy(day, now=NOW) == day


# --- garbage / boundary -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["星期六，三點去公園", "星期六到日本旅行", "星期六同一班朋友去公園", "", "Sunday 10am"],
)
def test_no_false_series_cues(text):
    assert not has_series_cue(text)


def test_policy_never_raises_on_odd_input():
    odd = _create("逢星期一至五 99:99-12:00 2月30日至3月1日", start=None, end=None)
    result = apply_series_policy(odd, now=NOW)
    assert result.intent_type in (IntentType.NEEDS_CLARIFICATION, IntentType.CREATE_EVENT)
