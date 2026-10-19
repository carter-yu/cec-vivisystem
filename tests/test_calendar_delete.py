"""Phase 28 delete-one (D1–D10). Fake calendar/confirmation stores; fixed HKT clock."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_delete import match_delete_candidates
from cec_vivisystem.calendar_writer import (
    FakeCalendarClient,
    GoogleCalendarClient,
    GoogleCalendarConfig,
    InMemoryCalendarAuditStore,
    write_calendar_delete,
)
from cec_vivisystem.confirmation import (
    InMemoryConfirmationStore,
    JsonDirConfirmationStore,
    classify_pick_reply,
    create_delete_confirmation,
    resolve_confirmation,
)
from cec_vivisystem.listener import process_slack_message_event
from cec_vivisystem.models import (
    CalendarDeleteTarget,
    CalendarListedEvent,
    CalendarWriteOutcome,
    ConfirmationStatus,
    IntentType,
    ParseResult,
)
from cec_vivisystem.parse_fallback import FakeLlmParser, parse_with_fallback
from cec_vivisystem.parser import parse

TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=TZ)  # Friday; 星期四 → 2026-10-08
CHANNEL = "C_PLANS"
CAL_ID = "family-cal"
SWIM = CalendarListedEvent(
    event_id="abcdef1234567890swim",
    summary="游泳",
    start=datetime(2026, 10, 8, 16, 0, tzinfo=TZ),
    end=datetime(2026, 10, 8, 17, 0, tzinfo=TZ),
    all_day=False,
    participants=["Cedric"],
)
SWIM_LATE = CalendarListedEvent(
    event_id="fedcba0987654321late",
    summary="游泳 (Coco)",
    start=datetime(2026, 10, 8, 18, 0, tzinfo=TZ),
    end=datetime(2026, 10, 8, 19, 0, tzinfo=TZ),
    all_day=False,
)
DENTIST = CalendarListedEvent(
    event_id="dentist0000000000001",
    summary="牙醫",
    start=datetime(2026, 10, 8, 10, 0, tzinfo=TZ),
    end=datetime(2026, 10, 8, 11, 0, tzinfo=TZ),
    all_day=False,
)


def _event(text: str, ts: str = "1791100000.000100", **extra) -> dict:
    return {
        "type": "message",
        "channel": CHANNEL,
        "user": "U_PARENT",
        "text": text,
        "ts": ts,
        "client_msg_id": f"msg-{ts}",
        **extra,
    }


def _reply(text: str, n: int = 1, thread: str = "1791100000.000100") -> dict:
    return _event(text, ts=f"17911000{n:02d}.000200", thread_ts=thread)


def _setup(events=None, **calendar_kwargs):
    store = InMemoryConfirmationStore()
    audit = InMemoryCalendarAuditStore()
    calendar = FakeCalendarClient(listed_events=events or [], **calendar_kwargs)
    kwargs = {
        "allowed_channel_ids": {CHANNEL},
        "confirmation_store": store,
        "calendar_client": calendar,
        "calendar_id": CAL_ID,
        "calendar_audit_store": audit,
        "now": NOW,
    }
    return store, audit, calendar, kwargs


def _target(event: CalendarListedEvent = SWIM) -> CalendarDeleteTarget:
    return CalendarDeleteTarget(event_id=event.event_id, calendar_id=CAL_ID,
                                summary=event.summary, start=event.start, end=event.end,
                                all_day=event.all_day)


def _accepted_delete(store=None, targets=None):
    store = store or InMemoryConfirmationStore()
    request = parse("刪除星期四游水", now=NOW)
    pending = create_delete_confirmation(request, targets or [_target()], store=store, now=NOW)
    return resolve_confirmation(pending.confirmation_id, "accept", store=store, now=NOW)


# --- parse ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "title", "participants"),
    [
        ("刪除星期四游水", "游泳", []),
        ("delete Cedric swim Thursday", "游泳", ["Cedric"]),
        ("取消10月8號 牙醫", "牙醫", []),
    ],
)
def test_delete_intent_parses_hints_without_writing(text, title, participants):
    result = parse(text, now=NOW)
    assert result.intent_type == IntentType.DELETE_EVENT
    assert result.title == title
    assert result.participants == participants
    assert result.start.date().isoformat() == "2026-10-08"


def test_delete_without_date_asks_for_date_and_lists_nothing():
    store, _audit, calendar, kwargs = _setup([SWIM])
    result = process_slack_message_event(_event("刪除游水"), **kwargs)
    assert result.parse_result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert "邊一日" in result.reply_text and "No calendar change was made" in result.reply_text
    assert calendar.list_calls == [] and store.list_all() == []


def test_simplified_delete_verb_is_not_understood():
    # Escaped negative fixture: the Simplified delete verb must not parse as delete.
    result = parse("删除星期四游水", now=NOW)
    assert result.intent_type != IntentType.DELETE_EVENT


# --- D1 unique match --------------------------------------------------------------


def test_d1_unique_match_proposes_title_start_short_id_without_delete():
    store, _audit, calendar, kwargs = _setup([SWIM, DENTIST])
    result = process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    text = result.reply_text
    assert "請確認刪除" in text
    assert "游泳 · 2026-10-08（四）16:00 HKT · id abcdef12" in text
    assert "No calendar change will be made until you confirm." in text
    assert calendar.delete_calls == []
    (pending,) = store.list_pending()
    assert [t.event_id for t in pending.delete_candidates] == [SWIM.event_id]
    (_cal, time_min, time_max) = calendar.list_calls[0]
    assert (time_min.isoformat(), time_max.isoformat()) == (
        "2026-10-08T00:00:00+08:00", "2026-10-09T00:00:00+08:00"
    )


# --- D2 yes deletes once ----------------------------------------------------------


def test_d2_yes_deletes_exactly_once_with_audit():
    _store, audit, calendar, kwargs = _setup([SWIM])
    process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    result = process_slack_message_event(_reply("yes"), **kwargs)
    assert calendar.delete_calls == [(CAL_ID, SWIM.event_id)]
    assert result.reply_text.startswith("已刪除：游泳")
    (row,) = audit.list_all()
    assert (row.op, row.outcome, row.calendar_event_id) == (
        "delete", CalendarWriteOutcome.SUCCESS, SWIM.event_id
    )


# --- D3 no match ------------------------------------------------------------------


def test_d3_no_match_replies_clearly_and_never_deletes():
    store, _audit, calendar, kwargs = _setup([DENTIST])
    result = process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    assert "搵唔到 2026-10-08 相符嘅「游泳」" in result.reply_text
    assert "No calendar change was made" in result.reply_text
    assert store.list_all() == [] and calendar.delete_calls == []


def test_list_failure_never_deletes():
    store, _audit, calendar, kwargs = _setup(fail_list_with=RuntimeError("synthetic"))
    result = process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    assert "未能讀取日曆" in result.reply_text
    assert store.list_all() == [] and calendar.delete_calls == []


# --- D4 ambiguous ------------------------------------------------------------------


def test_d4_ambiguous_numbered_pick_then_yes_deletes_only_choice():
    store, _audit, calendar, kwargs = _setup([SWIM, SWIM_LATE])
    listed = process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    assert "搵到 2 個相符活動" in listed.reply_text
    assert "1. 游泳 · 2026-10-08（四）16:00 HKT" in listed.reply_text
    assert "2. 游泳 (Coco) · 2026-10-08（四）18:00 HKT" in listed.reply_text

    premature = process_slack_message_event(_reply("yes", 1), **kwargs)
    assert "請先回覆編號" in premature.reply_text
    assert calendar.delete_calls == []
    assert store.list_pending()[0].status == ConfirmationStatus.PENDING

    picked = process_slack_message_event(_reply("2", 2), **kwargs)
    assert "請確認刪除" in picked.reply_text and "id fedcba09" in picked.reply_text
    assert calendar.delete_calls == []

    process_slack_message_event(_reply("yes", 3), **kwargs)
    assert calendar.delete_calls == [(CAL_ID, SWIM_LATE.event_id)]


def test_d4_out_of_range_pick_and_too_many_matches():
    _store, _audit, _calendar, kwargs = _setup([SWIM, SWIM_LATE])
    process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    bad = process_slack_message_event(_reply("7", 1), **kwargs)
    assert "1 至 2" in bad.reply_text
    many = [
        CalendarListedEvent(event_id=f"swim{i:02d}", summary="游泳",
                            start=datetime(2026, 10, 8, 8 + i, tzinfo=TZ), end=None,
                            all_day=False)
        for i in range(10)
    ]
    store2, _a, calendar2, kwargs2 = _setup(many)
    result = process_slack_message_event(_event("刪除星期四游水"), **kwargs2)
    assert "太多" in result.reply_text
    assert store2.list_all() == [] and calendar2.delete_calls == []


@pytest.mark.parametrize(("text", "expected"),
                         [("2", 2), ("第3個", 3), ("２", 2), ("#1", 1), ("10", None),
                          ("yes", None), ("2點", None)])
def test_pick_reply_classifier(text, expected):
    assert classify_pick_reply(text) == expected


def test_time_hint_narrows_and_participant_prefers():
    request = parse("刪除星期四6點游水", now=NOW)
    assert match_delete_candidates(request, [SWIM, SWIM_LATE], calendar_id=CAL_ID) == []
    request = parse("刪除星期四下午6點游水", now=NOW)
    (only,) = match_delete_candidates(request, [SWIM, SWIM_LATE], calendar_id=CAL_ID)
    assert only.event_id == SWIM_LATE.event_id
    request = parse("delete Cedric swim Thursday", now=NOW)
    (only,) = match_delete_candidates(request, [SWIM, SWIM_LATE], calendar_id=CAL_ID)
    assert only.event_id == SWIM.event_id


# --- D5 second yes / redelivery ---------------------------------------------------


def test_d5_second_yes_is_already_deleted_without_second_call():
    _store, audit, calendar, kwargs = _setup([SWIM])
    process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    process_slack_message_event(_reply("yes", 1), **kwargs)
    again = process_slack_message_event(_reply("yes", 2), **kwargs)
    assert len(calendar.delete_calls) == 1
    assert "已經刪除咗" in again.reply_text
    assert [r.outcome for r in audit.list_all()].count(CalendarWriteOutcome.ALREADY_DELETED) == 1


def test_d5_redelivered_request_reuses_confirmation():
    store, _audit, _calendar, kwargs = _setup([SWIM])
    process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    assert len(store.list_all()) == 1


# --- D6 Google 404 ----------------------------------------------------------------


def test_d6_google_404_is_soft_already_gone_with_audit():
    _store, audit, _calendar, kwargs = _setup([SWIM], missing_event_ids={SWIM.event_id})
    process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    result = process_slack_message_event(_reply("yes"), **kwargs)
    assert "已經唔存在" in result.reply_text
    (row,) = audit.list_all()
    assert (row.op, row.outcome) == ("delete", CalendarWriteOutcome.ALREADY_DELETED)


def _live_client(execute):
    events = SimpleNamespace(delete=Mock(return_value=SimpleNamespace(execute=execute)))
    client = GoogleCalendarClient(GoogleCalendarConfig("id", "secret", "refresh", CAL_ID))
    client._service = SimpleNamespace(events=Mock(return_value=events))
    return client, events


@pytest.mark.parametrize("status", [404, 410])
def test_d6_live_client_maps_gone_status_to_false(status):
    error = Exception("gone")
    error.resp = SimpleNamespace(status=status)
    client, events = _live_client(Mock(side_effect=error))
    assert client.delete_event(calendar_id=CAL_ID, event_id="evt") is False
    events.delete.assert_called_once_with(calendarId=CAL_ID, eventId="evt")


def test_live_client_delete_success_and_other_errors():
    client, _ = _live_client(Mock(return_value=""))
    assert client.delete_event(calendar_id=CAL_ID, event_id="evt") is True
    error = Exception("forbidden")
    error.resp = SimpleNamespace(status=403)
    client, _ = _live_client(Mock(side_effect=error))
    with pytest.raises(Exception, match="forbidden"):
        client.delete_event(calendar_id=CAL_ID, event_id="evt")


def test_google_failure_is_failed_and_retryable():
    store = InMemoryConfirmationStore()
    accepted = _accepted_delete(store)
    audit = InMemoryCalendarAuditStore()
    failing = FakeCalendarClient(fail_delete_with=RuntimeError("synthetic 500"))
    first = write_calendar_delete(accepted, client=failing, audit_store=audit)
    assert first.outcome == CalendarWriteOutcome.FAILED
    retry = FakeCalendarClient()
    assert write_calendar_delete(accepted, client=retry, audit_store=audit).outcome == (
        CalendarWriteOutcome.SUCCESS
    )


# --- D7 gate ----------------------------------------------------------------------


def test_d7_refuses_pending_missing_id_multi_target_and_create_confirmation():
    store = InMemoryConfirmationStore()
    request = parse("刪除星期四游水", now=NOW)
    pending = create_delete_confirmation(request, [_target()], store=store, now=NOW)
    calendar = FakeCalendarClient()
    assert write_calendar_delete(pending, client=calendar).error_type == "not_accepted"
    accepted = _accepted_delete()
    accepted.confirmation_id = ""
    assert write_calendar_delete(accepted, client=calendar).error_type == (
        "missing_confirmation_id"
    )
    two = _accepted_delete(targets=[_target(SWIM), _target(SWIM_LATE)])
    assert write_calendar_delete(two, client=calendar).error_type == "missing_target"
    create_like = _accepted_delete()
    create_like.parse_result.intent_type = IntentType.CREATE_EVENT
    assert write_calendar_delete(create_like, client=calendar).error_type == "not_delete_event"
    assert calendar.delete_calls == []


def test_delete_targets_round_trip_json_store(tmp_path):
    store = JsonDirConfirmationStore(tmp_path)
    accepted = _accepted_delete(store)
    loaded = store.get(accepted.confirmation_id)
    assert loaded.delete_candidates == [_target()]
    assert loaded.parse_result.intent_type == IntentType.DELETE_EVENT


# --- D8 create reject -------------------------------------------------------------


def test_d8_rejecting_create_never_deletes():
    _store, _audit, calendar, kwargs = _setup([SWIM])
    process_slack_message_event(_event("星期四下午4點帶 Cedric 去游泳"), **kwargs)
    result = process_slack_message_event(_reply("不要"), **kwargs)
    assert result.reply_text == "Rejected. No calendar change was made."
    assert calendar.delete_calls == [] and calendar.calls == []


def test_rejecting_delete_never_deletes():
    _store, _audit, calendar, kwargs = _setup([SWIM])
    process_slack_message_event(_event("刪除星期四游水"), **kwargs)
    process_slack_message_event(_reply("no"), **kwargs)
    assert calendar.delete_calls == []


# --- D9 model cannot delete -------------------------------------------------------


def test_d9_delete_route_never_calls_model():
    llm = FakeLlmParser(fail_with=AssertionError("delete must not reach the model"))
    result = parse_with_fallback("刪除星期四游水", now=NOW, llm=llm, llm_first=True)
    assert result.intent_type == IntentType.DELETE_EVENT
    assert llm.calls == []


def test_d9_model_delete_intent_is_sanitized_away():
    proposed = ParseResult(intent_type=IntentType.DELETE_EVENT, title="游泳",
                           start=SWIM.start, end=None, all_day=False, location=None,
                           raw_text="唔想去游水喇")
    llm = FakeLlmParser(proposed)
    _store, _audit, calendar, kwargs = _setup([SWIM])
    result = process_slack_message_event(_event("唔想去游水喇"), **kwargs, llm_parser=llm)
    assert result.parse_result.intent_type == IntentType.UNKNOWN
    assert calendar.delete_calls == [] and calendar.list_calls == []


# --- D10 other paths unchanged -----------------------------------------------------


def test_d10_list_and_create_paths_unchanged():
    store, _audit, calendar, kwargs = _setup([SWIM])
    listed = process_slack_message_event(_event("今日有乜"), **kwargs)
    assert listed.parse_result.intent_type == IntentType.LIST_EVENTS
    created = process_slack_message_event(
        _event("星期四下午4點帶 Cedric 去游泳", ts="1791100099.000100"), **kwargs
    )
    assert created.reply_text.startswith("Please confirm this calendar create proposal:")
    assert calendar.delete_calls == []
    assert all(c.parse_result.intent_type == IntentType.CREATE_EVENT for c in store.list_all())


def test_delete_without_calendar_is_unavailable():
    result = process_slack_message_event(
        _event("刪除星期四游水"), allowed_channel_ids={CHANNEL}, now=NOW,
        confirmation_store=InMemoryConfirmationStore(),
    )
    assert "Calendar delete is not configured" in result.reply_text


def test_delete_logs_op_delete(monkeypatch):
    logger = Mock()
    monkeypatch.setattr("cec_vivisystem.calendar_writer.logger", logger)
    write_calendar_delete(_accepted_delete(), client=FakeCalendarClient())
    (call,) = [c for c in logger.info.call_args_list if c.args == ("write_succeeded",)]
    assert call.kwargs["op"] == "delete"
    assert call.kwargs["calendar_event_id"] == SWIM.event_id
