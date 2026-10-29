"""Live bug 2026-10-29: a v6 list reply lost the clock written on every line.

The same five dated lines were pasted in #family-plans twice (13:00 on f07a73d,
21:09 on 3f9033a after PR #11). Both times every line was answered with 「有日期
但冇時間」 / "a date but no time" and no proposal. The provider reply is never
logged, so the cases below are every v6 shape that reproduces that one outcome:
a dropped start, an all-day start, and a correct start reported as a missing
time. All of them must now give five items, and a line that really has no clock
must still ask.

Titles are synthetic (ground rule 15). The dates, weekday labels, clock format,
line count and the 111-character length are the live message's.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_writer import (
    FakeCalendarClient,
    InMemoryCalendarAuditStore,
)
from cec_vivisystem.confirmation import InMemoryConfirmationStore
from cec_vivisystem.event_batch import REASON_MISSING_TIME, REASON_WEEKDAY_MISMATCH
from cec_vivisystem.listener import process_slack_message_event
from cec_vivisystem.models import IntentType
from cec_vivisystem.parse_fallback import (
    PROMPT_PATHS,
    FakePayloadLlmParser,
    _date_from_line,
    _events_payload_to_parse_result,
    llm_payload_to_parse_result,
    parse_with_fallback,
)

HK = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 29, 21, 9, tzinfo=HK)  # the second live attempt
CHANNEL = "C_PLANS"
TS = "1793000000.000100"

MSG = (
    "加以下events:\n"
    "11月6日（五）15:30 游泳課程\n"
    "11月10日（二）15:30 鋼琴課程\n"
    "11月13日（五）14:00 游泳課程\n"
    "11月20日（五）14:00 戶外活動時間\n"
    "11月27日（五）14:00 游泳課程"
)
LINES = MSG.splitlines()[1:]
CLOCKS = ["15:30", "15:30", "14:00", "14:00", "14:00"]
ROWS = [
    ("2026-11-06", "游泳課程", "FR"),
    ("2026-11-10", "鋼琴課程", "TU"),
    ("2026-11-13", "游泳課程", "FR"),
    ("2026-11-20", "戶外活動時間", "FR"),
    ("2026-11-27", "游泳課程", "FR"),
]
# Slack can deliver the same keystrokes with a no-break space, full-width digits
# and colon, or a zero-width character inside the clock.
NBSP_MSG = (
    MSG.replace(" ", " ").replace("15:30", "１５：３０").replace("14:00", "14\u200b:00")
)


def _items(
    msg: str = MSG,
    rows: list[tuple[str, str, str]] | None = None,
    *,
    start: str = "timed",
    all_day: bool = False,
    missing: tuple[str, ...] = (),
    date_field: bool = True,
    source: bool = True,
) -> list[dict]:
    """One v6 item per row, in the shape the live model might have returned."""
    lines = msg.splitlines()[1:]
    items = []
    for index, (day, title, weekday) in enumerate(rows or ROWS):
        if start == "timed":
            value = f"{day}T{CLOCKS[index]}:00+08:00"
        elif start == "midnight":
            value = f"{day}T00:00:00+08:00"
        elif start == "null":
            value = None
        else:  # pragma: no cover - guard against a typo in a case name
            raise AssertionError(start)
        items.append({
            "title": title, "start": value, "end": None, "all_day": all_day,
            "location": None, "participants": [], "missing_fields": list(missing),
            "series": None, "date": day if date_field else None,
            "stated_weekday": weekday, "source_text": lines[index] if source else None,
        })
    return items


def _payload(items: list[dict], *, intent: str = "create_event") -> dict:
    return {"intent_type": intent, "events": items, "over_cap": False,
            "confidence": "medium"}


def _event(text: str, ts: str = TS, **extra) -> dict:
    return {"type": "message", "channel": CHANNEL, "user": "U_PARENT", "text": text,
            "ts": ts, "client_msg_id": f"msg-{ts}", **extra}


def _listener(payload: dict):
    """The offline listener pipeline with a canned provider reply."""
    store, audit, calendar = (
        InMemoryConfirmationStore(), InMemoryCalendarAuditStore(), FakeCalendarClient()
    )
    kwargs = {
        "allowed_channel_ids": {CHANNEL}, "confirmation_store": store,
        "calendar_client": calendar, "calendar_audit_store": audit,
        "llm_parser": FakePayloadLlmParser(payload), "now": NOW,
    }
    return store, calendar, kwargs


def _map(payload: dict, *, msg: str = MSG):
    return _events_payload_to_parse_result(payload, raw_text=msg, now=NOW)


def test_live_message_shape_is_the_logged_one():
    # message_length=111 in the live log: no hidden characters were involved.
    assert len(MSG) == 111
    assert len(MSG.splitlines()) == 6


# --- every reply shape that reproduced 「冇時間」 now gives five items ------------


SHAPES = {
    # The model answered correctly; this must stay a proposal.
    "correct_timed": {},
    # PR #11's case: the clock was dropped and reported as missing.
    "start_null": {"start": "null", "missing": ("time",)},
    # dfde7fa's case: a midnight all-day start for a line with a clock.
    "all_day_midnight": {"start": "midnight", "all_day": True},
    "all_day_no_start": {"start": "null", "all_day": True, "missing": ("time",)},
    "all_day_with_clock": {"all_day": True},
    # Still open after dfde7fa: the written clock plus a self-reported gap.
    "timed_self_missing_time": {"missing": ("time",)},
    "timed_self_missing_start": {"missing": ("start",)},
    # Neither start nor date: the day is read from the item's own line.
    "no_start_no_date": {"start": "null", "date_field": False, "missing": ("time",)},
    "no_start_no_date_no_source": {
        "start": "null", "date_field": False, "source": False, "missing": ("time",),
    },
    "no_source_text": {"start": "null", "source": False, "missing": ("time",)},
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("msg", [MSG, NBSP_MSG], ids=["plain", "slack_width"])
def test_written_clocks_give_five_items(shape, msg):
    result = _map(_payload(_items(msg, **SHAPES[shape])), msg=msg)
    assert result.intent_type == IntentType.CREATE_EVENT
    assert list(result.batch_not_included) == []
    assert [item.start.strftime("%Y-%m-%d %H:%M") for item in result.batch] == [
        "2026-11-06 15:30", "2026-11-10 15:30", "2026-11-13 14:00",
        "2026-11-20 14:00", "2026-11-27 14:00",
    ]
    assert all(not item.all_day for item in result.batch)
    # One hour is the documented default, not an invented end time.
    assert [item.end.strftime("%H:%M") for item in result.batch] == [
        "16:30", "16:30", "15:00", "15:00", "15:00"]
    assert all(item.end_defaulted for item in result.batch)


@pytest.mark.parametrize("shape", ["correct_timed", "timed_self_missing_time",
                                   "all_day_midnight", "no_start_no_date"])
def test_listener_proposes_five_items_and_waits_for_yes(shape):
    store, calendar, kwargs = _listener(_payload(_items(**SHAPES[shape])))
    reply = process_slack_message_event(_event(MSG), **kwargs).reply_text
    assert reply.startswith("請確認以下 5 個活動")
    for number, clock in enumerate(CLOCKS, start=1):
        assert f"{number}. {ROWS[number - 1][0]}" in reply
        assert clock in reply
    # The Calendar gate is untouched: one pending confirmation, no write yet.
    assert len(store.list_all()) == 1
    assert calendar.calls == []


def test_yes_still_writes_only_after_confirmation():
    _store, calendar, kwargs = _listener(_payload(_items(missing=("time",))))
    process_slack_message_event(_event(MSG), **kwargs)
    assert calendar.calls == []
    reply = process_slack_message_event(
        _event("yes", ts="1793000001.000100", thread_ts=TS), **kwargs
    ).reply_text
    assert "已建立 5 個活動" in reply or "5" in reply
    assert len(calendar.calls) == 5


# --- a line that really has no clock still asks ---------------------------------


@pytest.mark.parametrize("line", [
    "11月6日（五） 游泳課程",
    "11月6日（五）下午3:30 游泳課程",
    "11月6日（五）15:30-16:30 游泳課程",
    "11月6日（五）15:30 至 16:30 游泳課程",
])
def test_no_or_ambiguous_clock_still_asks(line):
    msg = f"加以下events:\n{line}\n11月10日（二） 鋼琴課程"
    payload = _payload(_items(msg, ROWS[:2], start="null", missing=("time",)))
    result = _map(payload, msg=msg)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert result.missing_fields == ["time"]
    assert [n.reason for n in result.batch_not_included] == [REASON_MISSING_TIME] * 2


def test_time_question_tells_the_family_to_resend_the_whole_list():
    # Live 2026-10-29 13:01: the answer came back as a thread reply that listed
    # only the clocks. The question stores nothing, so the copy must ask for the
    # whole list again and say that a times-only reply does not work.
    msg = "加以下events:\n11月6日（五）15:30 游泳課程\n11月13日（五）游泳課程"
    rows = [ROWS[0], ("2026-11-13", "游泳課程", "FR")]
    items = _items(msg, rows)
    items[1].update(start=None, missing_fields=["time"])
    store, calendar, kwargs = _listener(_payload(items))
    reply = process_slack_message_event(_event(msg), **kwargs).reply_text
    assert reply.startswith("以下項目有日期但冇時間")
    assert "「11月13日（五）游泳課程」" in reply
    assert "例子 / Example: 11月6日 15:30 游泳課" in reply
    assert "只回覆時間唔會加入日曆，一定要再傳成個清單。" in reply
    assert "A reply that gives only the times does not work." in reply
    assert "我未建立任何提案。/ No proposal was made." in reply
    assert store.list_all() == [] and calendar.calls == []


def test_explicit_all_day_is_kept():
    msg = "加以下events:\n11月6日（五）全日 游泳課程\n11月10日（二）全日 鋼琴課程"
    payload = _payload(_items(msg, ROWS[:2], start="midnight", all_day=True))
    result = _map(payload, msg=msg)
    assert result.intent_type == IntentType.CREATE_EVENT
    assert all(item.all_day for item in result.batch)


def test_slash_date_without_a_model_date_is_never_guessed():
    # 6/11 is day/month in Hong Kong and month/day elsewhere, so the day is
    # not read locally; the item asks instead of risking the wrong month.
    msg = "加以下events:\n6/11 15:30 游泳課程\n10/11 15:30 鋼琴課程"
    payload = _payload(_items(msg, ROWS[:2], start="null", date_field=False,
                              source=False, missing=("time",)))
    result = _map(payload, msg=msg)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert {n.reason for n in result.batch_not_included} == {"missing_date"}


@pytest.mark.parametrize(("line", "expected"), [
    ("11月6日（五）15:30 游泳課程", date(2026, 11, 6)),
    ("11月6號（五）15:30 游泳課程", date(2026, 11, 6)),
    ("2026-11-06 15:30 游泳課程", date(2026, 11, 6)),
    ("2026年11月6日 15:30 游泳課程", date(2026, 11, 6)),
    # Day/month in Hong Kong writing, month/day elsewhere: never guessed.
    ("6/11 15:30 游泳課程", None),
    ("13月40日 15:30 游泳課程", None),
    ("15:30 游泳課程", None),
    (None, None),
])
def test_date_from_line_reads_only_unambiguous_written_days(line, expected):
    assert _date_from_line(line, now=NOW) == expected


def test_locally_read_date_keeps_the_year_of_now_and_reports_a_passed_day():
    # ADR 0018: a list never moves a passed date to next year.
    msg = "加以下events:\n1月6日（二）15:30 游泳課程\n11月10日（二）15:30 鋼琴課程"
    rows = [("2026-01-06", "游泳課程", "TU"), ROWS[1]]
    payload = _payload(_items(msg, rows, start="null", date_field=False,
                              missing=("time",)))
    result = _map(payload, msg=msg)
    assert result.intent_type == IntentType.CREATE_EVENT
    assert [n.reason for n in result.batch_not_included] == ["past"]
    assert [item.start.date().isoformat() for item in result.batch] == ["2026-11-10"]


def test_per_item_checks_still_run_after_a_clock_is_read():
    # The written weekday does not match the written day: the item is listed,
    # not silently created.
    rows = [(ROWS[0][0], ROWS[0][1], "MO"), *ROWS[1:]]
    result = _map(_payload(_items(rows=rows, start="null", missing=("time",))))
    assert [n.reason for n in result.batch_not_included] == [REASON_WEEKDAY_MISMATCH]
    assert len(result.batch) == 4


# --- the single-event path keeps the Phase 21 rule ------------------------------


def test_single_event_payload_with_a_contradicting_self_report_still_asks():
    # Phase 21 regression: one v5-shaped create that reports a gap it fills is
    # not promoted here, because no line is re-read on that path.
    payload = {
        "intent_type": "create_event", "title": "游泳課程",
        "start": "2026-11-06T15:30:00+08:00", "end": None, "all_day": False,
        "location": None, "participants": [], "missing_fields": ["start"],
        "confidence": "medium", "series": None,
    }
    result = llm_payload_to_parse_result(payload, raw_text="11月6日 15:30 游泳課程", now=NOW)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert result.missing_fields == ["start"]


def test_v5_rollback_payload_still_maps_to_one_create():
    payload = {
        "intent_type": "create_event", "title": "游泳課程",
        "start": "2026-11-06T15:30:00+08:00", "end": None, "all_day": False,
        "location": None, "participants": [], "missing_fields": [],
        "confidence": "medium", "series": None,
    }
    result = parse_with_fallback(
        "11月6日 15:30 游泳課程", now=NOW, llm=FakePayloadLlmParser(payload), llm_first=True
    )
    assert result.intent_type == IntentType.CREATE_EVENT
    assert result.batch is None
    assert result.start == datetime(2026, 11, 6, 15, 30, tzinfo=HK)


# --- diagnostics ----------------------------------------------------------------


def _evidence(logger: Mock) -> dict:
    call = next(
        c for c in logger.info.call_args_list if c.args == ("batch_item_evidence",)
    )
    return call.kwargs


@pytest.mark.parametrize(("shape", "expected"), [
    ("correct_timed", {"start_from_model": 5, "clock_recovered": 0,
                       "self_missing_time": 0, "item_flags": "1:sdtl"}),
    ("timed_self_missing_time", {"start_from_model": 5, "clock_recovered": 0,
                                 "self_missing_time": 5, "item_flags": "1:sdmtl"}),
    ("all_day_midnight", {"all_day_from_model": 5, "clock_recovered": 5,
                          "self_missing_time": 0, "item_flags": "1:sadtl+"}),
    ("start_null", {"start_from_model": 0, "clock_recovered": 5,
                    "self_missing_time": 5, "item_flags": "1:dmtl+"}),
    ("no_start_no_date", {"date_from_model": 0, "date_recovered": 5,
                          "clock_recovered": 5, "item_flags": "1:mtl+#"}),
])
def test_item_evidence_explains_each_shape(monkeypatch, shape, expected):
    logger = Mock()
    monkeypatch.setattr("cec_vivisystem.parse_fallback.logger", logger)
    _map(_payload(_items(**SHAPES[shape])))
    kwargs = _evidence(logger)
    assert kwargs["item_count"] == 5 and kwargs["dated_line_count"] == 5
    assert kwargs["component"] == "parse_fallback" and kwargs["outcome"] == "success"
    for name, value in expected.items():
        if name == "item_flags":
            assert kwargs["item_flags"].split()[0] == value
        else:
            assert kwargs[name] == value


def test_item_evidence_carries_no_message_content(monkeypatch):
    logger = Mock()
    monkeypatch.setattr("cec_vivisystem.parse_fallback.logger", logger)
    parse_with_fallback(
        MSG, now=NOW, correlation_id="corr-1", llm_first=True,
        llm=FakePayloadLlmParser(_payload(_items(missing=("time", "details")))),
    )
    kwargs = _evidence(logger)
    assert kwargs["correlation_id"] == "corr-1"
    assert kwargs["self_missing_values"] == "details,time"
    printed = " ".join(str(value) for value in kwargs.values())
    for secret in [*(row[1] for row in ROWS), "加以下events", "15:30", "11月6日"]:
        assert secret not in printed


def test_unknown_missing_names_are_not_logged_verbatim(monkeypatch):
    logger = Mock()
    monkeypatch.setattr("cec_vivisystem.parse_fallback.logger", logger)
    _map(_payload(_items(missing=("游泳課程 at 15:30",))))
    assert _evidence(logger)["self_missing_values"] == "other"


# --- prompt guards --------------------------------------------------------------


def test_item_schema_states_the_clock_rule_without_touching_v5():
    from cec_vivisystem.parse_fallback import CREATE_SCHEMA, EVENT_ITEM_SCHEMA

    item = EVENT_ITEM_SCHEMA["properties"]
    assert "Null only when" in item["start"]["description"]
    assert "Never name a field that this item fills." in item["missing_fields"]["description"]
    assert "全日" in item["all_day"]["description"]
    # The v5 rollback request keeps its own property objects.
    assert "description" not in CREATE_SCHEMA["properties"]["start"]
    assert "description" not in CREATE_SCHEMA["properties"]["missing_fields"]


def test_v6_prompt_states_the_clock_rule_and_stays_bounded():
    text = PROMPT_PATHS["v6"].read_text(encoding="utf-8")
    assert "Never report a field that the same item fills." in text
    assert "A line that\nwrites a clock" in text
    assert "A list always uses create_event" in text
    assert "never move a passed date to next year" in text
    assert len(text.split()) < 600  # keeps the 15 s per-model budget realistic
