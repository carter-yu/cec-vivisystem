"""Phase 33c repeating items inside a multi-event list (ADR 0018 amendment).

Fake model / vision / calendar; fixed HKT clock; temporary stores; no network.
Every fixture is synthetic (ground rule 15): the flyer mirrors only the shape
of a term timetable (one month, one shared daily window, one class per
weekday); the titles are invented.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_writer import (
    FakeCalendarClient,
    InMemoryCalendarAuditStore,
    _draft_to_google_event,
    batch_child_key,
    batch_series_child_key,
    write_calendar_batch_create,
    write_calendar_create,
    write_calendar_series_create,
)
from cec_vivisystem.confirmation import (
    InMemoryConfirmationStore,
    JsonDirConfirmationStore,
    build_batch_proposal,
    create_confirmation,
    resolve_confirmation,
)
from cec_vivisystem.event_batch import (
    MAX_BATCH_EVENTS,
    MAX_OCCURRENCES_HINT,
    REASON_COPY,
    batch_event_count,
    expand_batch_items,
    expand_item,
    item_occurrences,
)
from cec_vivisystem.listener import process_slack_message_event
from cec_vivisystem.models import (
    CalendarListedEvent,
    CalendarWriteOutcome,
    ConfirmationStatus,
    EventItem,
    IntentType,
    SeriesSpec,
)
from cec_vivisystem.parse_fallback import FakePayloadLlmParser
from cec_vivisystem.parser import contains_simplified_markers
from cec_vivisystem.series import MAX_OCCURRENCES, load_batch_series_enabled

TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=TZ)  # Friday
CHANNEL = "C_PLANS"
TS = "1791000000.000100"

# --- the synthetic flyer -------------------------------------------------------

FLYER_TEXT = (
    "第二期課堂\n"
    "26年11月2日至11月30日（上學日）\n"
    "逢上學日（星期一至五）下午3:15-4:15\n"
    "美術班（星期一）\n"
    "音樂班（星期二）\n"
    "舞蹈班（星期三）\n"
    "故事班（星期四）\n"
    "科學班（星期五）"
)
FLYER_CLASSES = [
    ("美術班", "MO", "美術班（星期一）", 5),
    ("音樂班", "TU", "音樂班（星期二）", 4),
    ("舞蹈班", "WE", "舞蹈班（星期三）", 4),
    ("故事班", "TH", "故事班（星期四）", 4),
    ("科學班", "FR", "科學班（星期五）", 4),
]
FLYER_HEADER_LINE = "逢上學日（星期一至五）下午3:15-4:15"
FLYER_EVENTS = 21


def _series(
    weekdays: list[str],
    *,
    range_start: str = "2026-11-02",
    range_end: str | None = "2026-11-30",
    start_time: str = "15:15",
    end_time: str | None = "16:15",
) -> dict:
    return {
        "weekdays": weekdays,
        "range_start": range_start,
        "range_end": range_end,
        "start_time": start_time,
        "end_time": end_time,
    }


def _item(title, *, day=None, start=None, end=None, source=None, series=None,
          weekday=None, **overrides) -> dict:
    item = {
        "title": title,
        "start": f"{day}T{start}:00+08:00" if (day and start) else None,
        "end": f"{day}T{end}:00+08:00" if (day and end) else None,
        "all_day": False,
        "location": None,
        "participants": [],
        "missing_fields": [] if (series is not None or start) else ["time"],
        "series": series,
        "date": day,
        "stated_weekday": weekday,
        "source_text": source,
    }
    item.update(overrides)
    return item


def _class_item(title, weekday, source, **series_kwargs) -> dict:
    return _item(title, source=source, weekday=weekday,
                 series=_series([weekday], **series_kwargs))


def _flyer_items(*, with_header: bool = True) -> list[dict]:
    items = [_class_item(title, wd, src) for title, wd, src, _ in FLYER_CLASSES]
    if with_header:
        items.append(_item(
            None, source=FLYER_HEADER_LINE, missing_fields=["title"],
            series=_series(["MO", "TU", "WE", "TH", "FR"]),
        ))
    return items


def _payload(items: list[dict], *, over_cap: bool = False, intent="create_event") -> dict:
    return {"intent_type": intent, "events": items, "over_cap": over_cap, "confidence": "medium"}


def _event(text: str, ts: str = TS, **extra) -> dict:
    return {
        "type": "message",
        "channel": CHANNEL,
        "user": "U_PARENT",
        "text": text,
        "ts": ts,
        "client_msg_id": f"msg-{ts}",
        **extra,
    }


def _reply(text: str, n: int = 1) -> dict:
    return _event(text, ts=f"179100000{n}.000100", thread_ts=TS)


def _setup(payload=None, calendar=None, **extra):
    store, audit = InMemoryConfirmationStore(), InMemoryCalendarAuditStore()
    calendar = calendar or FakeCalendarClient()
    kwargs = {
        "allowed_channel_ids": {CHANNEL},
        "confirmation_store": store,
        "calendar_client": calendar,
        "calendar_audit_store": audit,
        "llm_parser": FakePayloadLlmParser(payload or _payload(_flyer_items())),
        "now": NOW,
        **extra,
    }
    return store, audit, calendar, kwargs


def _propose(payload=None, text=FLYER_TEXT, calendar=None, **extra):
    store, audit, calendar, kwargs = _setup(payload, calendar, **extra)
    reply = process_slack_message_event(_event(text), **kwargs).reply_text
    return reply, store, audit, calendar, kwargs


def _spec(weekdays=(0,), range_start=date(2026, 11, 2), range_end=date(2026, 11, 30),
          start_time=time(15, 15), end_time=time(16, 15)) -> SeriesSpec:
    return SeriesSpec(weekdays=tuple(weekdays), range_start=range_start, range_end=range_end,
                      start_time=start_time, end_time=end_time)


def _stored_item(index=1, title="美術班", spec=None, count=5, **overrides) -> EventItem:
    """A stored item whose ``start`` is its spec's first matching day."""
    spec = _spec() if spec is None else spec
    day = spec.range_start
    while spec.weekdays and day.weekday() not in spec.weekdays:
        day += timedelta(days=1)
    start = datetime.combine(day, spec.start_time, tzinfo=TZ)
    end = (datetime.combine(day, spec.end_time, tzinfo=TZ)
           if spec.end_time is not None else start + timedelta(hours=1))
    defaults = {
        "index": index, "title": title, "start": start, "end": end,
        "all_day": False, "series": spec, "occurrence_count": count,
    }
    defaults.update(overrides)
    return EventItem(**defaults)


def _accepted(items, *, dropped=(), store=None):
    """A stored, accepted list confirmation holding ``items``."""
    store = store or InMemoryConfirmationStore()
    payload_items = tuple(items)
    first = payload_items[0]
    from cec_vivisystem.models import Confidence, ParseResult

    parse_result = ParseResult(
        intent_type=IntentType.CREATE_EVENT, title=first.title, start=first.start,
        end=first.end, all_day=False, location=None, raw_text="synthetic",
        confidence=Confidence.MEDIUM, notes="llm_first;batch", batch=payload_items,
    )
    confirmation = create_confirmation(
        parse_result, store=store, now=NOW, channel_id=CHANNEL, source_message_id=TS,
    )
    if dropped:
        confirmation.batch_dropped = list(dropped)
        store.save(confirmation)
    return resolve_confirmation(confirmation.confirmation_id, "accept", store=store, now=NOW)


# --- K: the kill switch --------------------------------------------------------


def test_k1_switch_off_keeps_todays_repeating_refusal(monkeypatch):
    monkeypatch.setenv("CEC_BATCH_SERIES", "off")
    reply, store, _, calendar, _ = _propose()
    assert reply.startswith("呢個訊息入面冇一項可以加入日曆。/ No event in this message can be proposed.")
    assert "未包括 / Not included (6):" in reply
    assert reply.count("重複活動，請另外傳送 / repeating schedule: send it as its own message") == 6
    assert store.list_all() == [] and calendar.calls == []


def test_k1b_switch_defaults_on_and_unknown_values_stay_on(monkeypatch):
    assert load_batch_series_enabled({}) is True
    assert load_batch_series_enabled({"CEC_BATCH_SERIES": "off"}) is False
    assert load_batch_series_enabled({"CEC_BATCH_SERIES": "OFF"}) is False
    assert load_batch_series_enabled({"CEC_BATCH_SERIES": "on"}) is True
    assert load_batch_series_enabled({"CEC_BATCH_SERIES": "maybe"}) is True
    monkeypatch.delenv("CEC_BATCH_SERIES", raising=False)
    assert load_batch_series_enabled() is True


def test_k2_one_off_list_is_unchanged_with_the_switch_on():
    text = "11月6日 9:00 急救課程\n11月13日 9:00 考試"
    payload = _payload([
        _item("急救課程", day="2026-11-06", start="09:00", source="11月6日 9:00 急救課程"),
        _item("考試", day="2026-11-13", start="09:00", source="11月13日 9:00 考試"),
    ])
    reply, store, _, calendar, kwargs = _propose(payload, text=text)
    assert reply.startswith(
        "請確認以下 2 個活動（會分開建立 2 個日曆活動）/ "
        "Please confirm these 2 events (2 separate calendar events):"
    )
    assert "重複" not in reply and "學校假期" not in reply
    assert 'Reply yes to create all 2, "drop 2" to remove item 2, or no to cancel.' in reply
    process_slack_message_event(_reply("yes"), **kwargs)
    (conf,) = store.list_all()
    assert [d.confirmation_id for d in calendar.calls] == [
        batch_child_key(conf.confirmation_id, 1), batch_child_key(conf.confirmation_id, 2),
    ]


def test_k3_switch_flipped_off_before_yes_refuses_visibly(monkeypatch):
    confirmation = _accepted([_stored_item()])
    calendar, audit = FakeCalendarClient(), InMemoryCalendarAuditStore()
    monkeypatch.setenv("CEC_BATCH_SERIES", "off")
    result = write_calendar_batch_create(
        confirmation, client=calendar, audit_store=audit, now=NOW,
    )
    assert result.outcome == CalendarWriteOutcome.REFUSED
    assert result.error_type == "series_disabled"
    assert calendar.calls == []
    assert [r.outcome for r in audit.list_all()] == [CalendarWriteOutcome.REFUSED]


# --- E: extraction -------------------------------------------------------------


def test_e1_flyer_gives_five_items_and_twentyone_events():
    _, store, _, _, _ = _propose()
    (conf,) = store.list_all()
    items = conf.parse_result.batch
    assert [i.title for i in items] == [c[0] for c in FLYER_CLASSES]
    assert [i.occurrence_count for i in items] == [c[3] for c in FLYER_CLASSES]
    assert batch_event_count(items) == FLYER_EVENTS
    assert items[0].start == datetime(2026, 11, 2, 15, 15, tzinfo=TZ)
    assert items[0].end == datetime(2026, 11, 2, 16, 15, tzinfo=TZ)
    assert conf.parse_result.series is None
    assert [n.reason for n in conf.parse_result.batch_not_included] == ["missing_title"]


def test_e2_lone_series_item_still_takes_the_phase_27_path():
    payload = _payload([_class_item("美術班", "MO", "美術班（星期一）")])
    reply, store, _, _, _ = _propose(payload, text="逢星期一 15:15-16:15，11月2日至11月30日 美術班")
    assert reply.startswith("請確認以下重複活動提案（會分開建立 5 個活動）")
    assert "• 共 5 次 / 5 occurrences:" in reply
    (conf,) = store.list_all()
    assert conf.parse_result.batch is None
    assert conf.parse_result.series is not None


def test_e3_mixed_one_off_and_repeating_items_give_one_proposal():
    text = "11月6日 9:00 急救課程\n11月13日 9:00 考試\n美術班（星期一）\n音樂班（星期二）"
    payload = _payload([
        _item("急救課程", day="2026-11-06", start="09:00", source="11月6日 9:00 急救課程"),
        _item("考試", day="2026-11-13", start="09:00", source="11月13日 9:00 考試"),
        _class_item("美術班", "MO", "美術班（星期一）"),
        _class_item("音樂班", "TU", "音樂班（星期二）"),
    ])
    reply, store, _, calendar, kwargs = _propose(payload, text=text)
    assert reply.startswith(
        "請確認以下 4 項（會分開建立 11 個日曆活動）/ "
        "Please confirm these 4 items (11 separate calendar events):"
    )
    assert "1. 2026-11-06（五）09:00–10:00 急救課程" in reply
    assert "3. 美術班 · 逢星期一 15:15–16:15 · 2026-11-02 至 2026-11-30 · 共 5 次" in reply
    assert "回覆 yes 確認建立以上 11 個活動" in reply
    (conf,) = store.list_all()
    assert batch_event_count(conf.parse_result.batch) == 11
    process_slack_message_event(_reply("yes"), **kwargs)
    keys = [d.confirmation_id for d in calendar.calls]
    assert len(keys) == 11
    assert keys[0] == batch_child_key(conf.confirmation_id, 1)
    assert keys[2] == batch_series_child_key(conf.confirmation_id, 3, date(2026, 11, 2))


def test_e4_v5_payload_rollback_path_is_unchanged():
    payload = {
        "intent_type": "create_event", "title": "游水", "start": "2026-11-06T15:30:00+08:00",
        "end": None, "all_day": False, "location": None, "participants": [],
        "missing_fields": [], "confidence": "medium",
    }
    reply, store, _, _, _ = _propose(payload, text="11月6日 15:30 游水")
    assert reply.startswith("Please confirm this calendar create proposal:")
    (conf,) = store.list_all()
    assert conf.parse_result.batch is None and conf.parse_result.series is None


def test_e5_series_item_claiming_all_day_is_not_included():
    items = _flyer_items(with_header=False)
    items[0]["all_day"] = True
    reply, store, _, _, _ = _propose(_payload(items))
    assert "未包括 / Not included (1):" in reply
    assert "多日全日活動，請逐日傳送 / multi-day all-day: send each day" in reply
    (conf,) = store.list_all()
    assert len(conf.parse_result.batch) == 4


# --- V: per-item validation ----------------------------------------------------


def _one_class(**series_kwargs) -> dict:
    return _payload([
        _class_item("美術班", "MO", "美術班（星期一）", **series_kwargs),
        _class_item("音樂班", "TU", "音樂班（星期二）"),
    ])


def test_v1_series_with_no_end_date_asks_for_the_range():
    reply, store, _, _, _ = _propose(_one_class(range_end=None),
                                     text="美術班（星期一）\n音樂班（星期二）")
    assert "重複活動冇寫結束日期，請寫明日期範圍 / " \
           "repeating item has no end date: give the date range" in reply
    (conf,) = store.list_all()
    assert [i.title for i in conf.parse_result.batch] == ["音樂班"]


def test_v2_over_forty_occurrences_is_not_included_with_the_cap_in_the_copy():
    reply, _, _, _, _ = _propose(
        _one_class(range_start="2026-11-02", range_end="2027-12-31"),
        text="美術班（星期一）\n音樂班（星期二）",
    )
    assert f"重複多過 {MAX_OCCURRENCES_HINT} 次，請縮短日期範圍" in reply
    assert MAX_OCCURRENCES_HINT == MAX_OCCURRENCES


def test_v3_weekday_never_in_the_range_is_not_included():
    reply, _, _, _, _ = _propose(
        _one_class(range_start="2026-11-03", range_end="2026-11-07"),
        text="美術班（星期一）\n音樂班（星期二）",
    )
    assert "日期範圍內冇講明嘅星期 / the range has none of those weekdays" in reply


def test_v4_daily_window_crossing_midnight_is_not_included():
    reply, _, _, _, _ = _propose(
        _one_class(start_time="23:00", end_time="01:00"),
        text="美術班（星期一）\n音樂班（星期二）",
    )
    assert "時間跨日 / time crosses midnight" in reply


def test_v5_reversed_range_is_unclear():
    # An empty weekday list never reaches here: the payload validator already
    # rejects it, so the whole reply falls back (existing ADR 0018 contract).
    payload = _payload([
        _item("美術班", source="美術班（星期一）",
              series=_series(["MO"], range_start="2026-11-30", range_end="2026-11-02")),
        _class_item("音樂班", "TU", "音樂班（星期二）"),
    ])
    reply, _, _, _, _ = _propose(payload, text="美術班（星期一）\n音樂班（星期二）")
    assert "資料唔清楚 / details are unclear" in reply


def test_v5b_empty_weekday_list_is_rejected_by_the_payload_validator():
    from cec_vivisystem.parse_fallback import validate_create_payload

    payload = _payload([_item("美術班", source="美術班", series=_series([]))])
    with pytest.raises(ValueError, match="series weekdays"):
        validate_create_payload(payload)


def test_v6_every_occurrence_in_the_past_is_not_included():
    reply, store, _, _, _ = _propose(
        _one_class(range_start="2026-09-01", range_end="2026-09-30"),
        text="美術班（星期一）\n音樂班（星期二）",
    )
    assert "日期已過 / date has passed" in reply
    (conf,) = store.list_all()
    assert [i.title for i in conf.parse_result.batch] == ["音樂班"]


def test_v7_started_timetable_is_trimmed_and_says_how_many_passed():
    # Mondays 2026-10-12 .. 2026-11-16: 12, 19, 26 passed; 2, 9, 16 remain.
    payload = _payload([
        _class_item("美術班", "MO", "美術班（星期一）",
                    range_start="2026-10-12", range_end="2026-11-16"),
        _class_item("音樂班", "TU", "音樂班（星期二）",
                    range_start="2026-11-03", range_end="2026-11-24"),
    ])
    reply, store, _, calendar, kwargs = _propose(
        payload, text="美術班（星期一）\n音樂班（星期二）",
    )
    assert "1. 美術班 · 逢星期一 15:15–16:15 · 2026-11-02 至 2026-11-16 · 共 3 次" in reply
    assert ("注意：第 1 項有 3 次已經過去，唔會建立 / "
            "Warning: 3 events of item 1 have passed. I will not make them.") in reply
    (conf,) = store.list_all()
    item = conf.parse_result.batch[0]
    assert item.occurrence_count == 3
    assert item.series.range_start == date(2026, 10, 12)  # the stated range is kept
    process_slack_message_event(_reply("yes"), **kwargs)
    first_item_dates = [d.occurrence_date for d in calendar.calls if d.batch_index == 1]
    assert first_item_dates == [date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16)]


def test_v8_stated_weekday_outside_the_series_is_a_mismatch():
    payload = _payload([
        _item("美術班", source="美術班（星期二）", weekday="TU", series=_series(["WE"])),
        _class_item("音樂班", "TU", "音樂班（星期二）"),
    ])
    reply, store, _, _, _ = _propose(payload, text="美術班（星期二）\n音樂班（星期二）")
    assert "星期幾同日期唔夾 / weekday does not match the date" in reply
    (conf,) = store.list_all()
    assert [i.title for i in conf.parse_result.batch] == ["音樂班"]


def test_v9_multi_weekday_item_with_no_stated_weekday_is_included():
    payload = _payload([
        _item("游水班", source="游水班（逢一三五）", series=_series(["MO", "WE", "FR"])),
        _class_item("音樂班", "TU", "音樂班（星期二）"),
    ])
    reply, store, _, _, _ = _propose(payload, text="游水班（逢一三五）\n音樂班（星期二）")
    assert "1. 游水班 · 逢星期一、三、五 15:15–16:15" in reply
    (conf,) = store.list_all()
    assert len(conf.parse_result.batch) == 2


def test_v10_over_the_event_cap_refuses_the_whole_message_without_truncating():
    # Two Mon–Fri items of 40 occurrences each = 80 events.
    long_range = {"range_start": "2026-11-02", "range_end": "2026-12-25"}
    payload = _payload([
        _item("A 班", source="A 班", series=_series(["MO", "TU", "WE", "TH"], **long_range)),
        _item("B 班", source="B 班", series=_series(["MO", "TU", "WE", "TH"], **long_range)),
    ])
    reply, store, _, calendar, _ = _propose(payload, text="A 班（逢一至四）\nB 班（逢一至四）")
    assert reply.startswith(f"呢個訊息會建立 64 個活動，一次最多 {MAX_BATCH_EVENTS} 個。")
    assert "我冇建立任何提案。" in reply
    assert "未有改動日曆。/ No calendar change was made." in reply
    assert store.list_all() == [] and calendar.calls == []


def test_v11_exactly_sixty_events_is_proposed():
    # Mon+Tue 2026-11-02 .. 2027-01-26 = 26 + 2 x 17 … build 60 exactly instead:
    # three Mon–Fri classes over four weeks = 3 x 20 = 60.
    four_weeks = {"range_start": "2026-11-02", "range_end": "2026-11-27"}
    payload = _payload([
        _item(f"第 {n} 班", source=f"第 {n} 班",
              series=_series(["MO", "TU", "WE", "TH", "FR"], **four_weeks))
        for n in range(1, 4)
    ])
    reply, store, _, _, _ = _propose(payload, text="第 1 班\n第 2 班\n第 3 班")
    assert f"（會分開建立 {MAX_BATCH_EVENTS} 個日曆活動）" in reply
    (conf,) = store.list_all()
    assert batch_event_count(conf.parse_result.batch) == MAX_BATCH_EVENTS


def test_v12_item_cap_is_checked_before_the_event_cap():
    items = [_class_item(f"第 {n} 班", "MO", f"第 {n} 班") for n in range(1, 17)]
    reply, store, _, _, _ = _propose(_payload(items), text="\n".join(f"第 {n} 班" for n in range(1, 17)))
    assert reply.startswith("一次最多加 15 個活動")
    assert store.list_all() == []


def test_v13_simplified_title_on_a_series_item_is_refused():
    # The family's message is Traditional (a Simplified message never reaches
    # the model at all); the model's own title is the Simplified one.
    payload = _payload([
        _item("美术课", source=None, series=_series(["MO"])),
        _class_item("音樂班", "TU", "音樂班（星期二）"),
    ])
    reply, _, _, _, _ = _propose(payload, text="美術課（星期一）\n音樂班（星期二）")
    assert "唔支援簡體字 / Simplified Chinese not supported" in reply


# --- P: proposal copy ----------------------------------------------------------


def test_p1_flyer_proposal_matches_the_template():
    reply, _, _, _, _ = _propose()
    lines = reply.splitlines()
    assert lines[0] == (
        "請確認以下 5 個重複活動（會分開建立 21 個日曆活動）/ "
        "Please confirm these 5 repeating items (21 separate calendar events):"
    )
    assert lines[1] == "1. 美術班 · 逢星期一 15:15–16:15 · 2026-11-02 至 2026-11-30 · 共 5 次"
    assert lines[2] == "2. 音樂班 · 逢星期二 15:15–16:15 · 2026-11-03 至 2026-11-24 · 共 4 次"
    assert lines[5] == "5. 科學班 · 逢星期五 15:15–16:15 · 2026-11-06 至 2026-11-27 · 共 4 次"
    assert lines[6] == "未包括 / Not included (1):"
    assert lines[7] == f"• 「{FLYER_HEADER_LINE}」— 冇活動名稱 / no activity name"
    assert reply.endswith("No calendar change will be made until you confirm.")


def test_p2_holiday_warning_appears_once_and_nothing_claims_a_write():
    reply = _propose()[0]
    warning = (
        "注意：學校假期同公眾假期我分唔到，呢 21 日全部會建立，請核對 / "
        "Warning: I do not know the school holidays or the public holidays. "
        "I will make all 21 events. Please check the dates."
    )
    assert reply.count(warning) == 1
    assert "已建立" not in reply and "Created" not in reply


def test_p3_fifteen_items_of_sixty_events_stay_under_the_slack_budget():
    items = [
        _stored_item(index=n, title=f"第 {n} 班",
                     spec=_spec(weekdays=(n % 5,), range_start=date(2026, 11, 2),
                                range_end=date(2026, 11, 27)),
                     count=4)
        for n in range(1, 16)
    ]
    from cec_vivisystem.models import (
        BatchOverlapNote,
        Confidence,
        NotIncludedItem,
        ParseResult,
    )

    parse_result = ParseResult(
        intent_type=IntentType.CREATE_EVENT, title=items[0].title, start=items[0].start,
        end=items[0].end, all_day=False, location=None, raw_text="synthetic",
        confidence=Confidence.MEDIUM, notes="llm_first;batch", batch=tuple(items),
        batch_not_included=tuple(
            NotIncludedItem(reason="series_open_ended", source_text=f"未知 {n}")
            for n in range(10)
        ),
    )
    notes = [BatchOverlapNote(item_index=n, line=f"第 {n} 項 4 次中 4 次撞期（例如 X）")
             for n in range(1, 11)]
    text = build_batch_proposal(parse_result, overlap_notes=notes)
    assert batch_event_count(items) == MAX_BATCH_EVENTS
    assert len(text) < 4000


def test_p4_rendered_copy_has_no_simplified_markers():
    assert not contains_simplified_markers(_propose()[0])


def test_p5_image_sourced_flyer_keeps_the_image_header():
    from cec_vivisystem.event_batch import IMAGE_PROPOSAL_HEADER
    from cec_vivisystem.image_extract import build_image_evidence
    from cec_vivisystem.parse_fallback import _events_payload_to_parse_result

    payload, evidence = build_image_evidence(_payload(_flyer_items()))
    result = _events_payload_to_parse_result(
        payload, raw_text="加event", now=NOW, evidence_text=evidence,
    )
    result.notes = f"{result.notes};from_image"
    text = build_batch_proposal(result)
    assert text.splitlines()[0] == IMAGE_PROPOSAL_HEADER
    assert "（會分開建立 21 個日曆活動）" in text


def test_p6_phase_27_single_series_proposal_still_shows_five_samples():
    payload = _payload([_class_item("美術班", "MO", "美術班（星期一）")])
    reply, _, _, _, _ = _propose(payload, text="逢星期一 15:15-16:15，11月2日至11月30日 美術班")
    assert reply.count("  • 2026-") == 5
    assert "…另外 0 次" not in reply


# --- N: narrowing --------------------------------------------------------------


def test_n1_dropping_a_repeating_item_removes_all_of_its_occurrences():
    _, store, _, calendar, kwargs = _propose()
    edited = process_slack_message_event(_reply("唔要 5"), **kwargs).reply_text
    assert edited.startswith("請確認以下 4 個重複活動（會分開建立 17 個日曆活動）")
    assert "已剔走 / Removed: 第 5 項（4 次）" in edited
    assert "科學班" not in edited
    (conf,) = store.list_all()
    assert conf.status == ConfirmationStatus.PENDING
    assert conf.batch_dropped == [5]
    assert conf.proposal_text == edited
    assert calendar.calls == []
    ack = process_slack_message_event(_reply("yes", 2), **kwargs)
    assert ack.reply_text == "已確認。已建立 17 個活動。/ Accepted. 17 calendar events created."
    assert len(calendar.calls) == 17


def test_n2_keep_only_one_item_updates_the_event_count():
    _, _, _, calendar, kwargs = _propose()
    edited = process_slack_message_event(_reply("只要 1"), **kwargs).reply_text
    assert edited.startswith("請確認以下 1 個重複活動（會分開建立 5 個日曆活動）")
    assert "回覆 yes 確認建立以上 5 個活動" in edited
    assert calendar.calls == []
    process_slack_message_event(_reply("yes", 2), **kwargs)
    assert len(calendar.calls) == 5
    assert {d.batch_index for d in calendar.calls} == {1}


def test_n3_removing_every_item_is_refused_and_state_is_unchanged():
    _, store, _, calendar, kwargs = _propose()
    before = store.list_all()[0].proposal_text
    refusal = process_slack_message_event(_reply("唔要 1,2,3,4,5"), **kwargs).reply_text
    assert "剔走晒" in refusal or "不要" in refusal
    (conf,) = store.list_all()
    assert conf.batch_dropped == [] and conf.proposal_text == before
    assert calendar.calls == []


def test_n4_narrowing_after_acceptance_writes_nothing():
    _, store, _, calendar, kwargs = _propose()
    process_slack_message_event(_reply("yes"), **kwargs)
    created = len(calendar.calls)
    process_slack_message_event(_reply("唔要 5", 2), **kwargs)
    assert len(calendar.calls) == created
    assert store.list_all()[0].status == ConfirmationStatus.ACCEPTED


# --- W: the writer -------------------------------------------------------------


def test_w1_yes_creates_every_occurrence_in_item_then_date_order():
    _, store, _, calendar, kwargs = _propose()
    ack = process_slack_message_event(_reply("yes"), **kwargs)
    assert ack.reply_text == "已確認。已建立 21 個活動。/ Accepted. 21 calendar events created."
    (conf,) = store.list_all()
    keys = [d.confirmation_id for d in calendar.calls]
    assert len(keys) == FLYER_EVENTS
    assert keys[0] == batch_series_child_key(conf.confirmation_id, 1, date(2026, 11, 2))
    assert keys[4] == batch_series_child_key(conf.confirmation_id, 1, date(2026, 11, 30))
    assert keys[-1] == batch_series_child_key(conf.confirmation_id, 5, date(2026, 11, 27))
    body = _draft_to_google_event(calendar.calls[0])
    assert body["id"] == hashlib.sha256(("cec-confirmation:" + keys[0]).encode()).hexdigest()


def test_w2_each_occurrence_has_one_audit_row_and_the_item_series_id():
    _, store, audit, calendar, kwargs = _propose()
    process_slack_message_event(_reply("yes"), **kwargs)
    (conf,) = store.list_all()
    rows = audit.list_all()
    assert len(rows) == FLYER_EVENTS
    assert {r.op for r in rows} == {"create"}
    private = _draft_to_google_event(calendar.calls[1])["extendedProperties"]["private"]
    assert private == {
        "confirmation_id": batch_series_child_key(conf.confirmation_id, 1, date(2026, 11, 9)),
        "parent_confirmation_id": conf.confirmation_id,
        "occurrence_date": "2026-11-09",
        "series_id": batch_child_key(conf.confirmation_id, 1),
        "batch_index": "1",
    }


def test_w3_a_one_off_item_in_the_same_list_keeps_its_phase_33a_key():
    text = "11月6日 9:00 急救課程\n美術班（星期一）"
    payload = _payload([
        _item("急救課程", day="2026-11-06", start="09:00", source="11月6日 9:00 急救課程"),
        _class_item("美術班", "MO", "美術班（星期一）"),
    ])
    _, store, _, calendar, kwargs = _propose(payload, text=text)
    process_slack_message_event(_reply("yes"), **kwargs)
    (conf,) = store.list_all()
    first = _draft_to_google_event(calendar.calls[0])
    assert first["extendedProperties"]["private"] == {
        "confirmation_id": batch_child_key(conf.confirmation_id, 1),
        "parent_confirmation_id": conf.confirmation_id,
        "batch_index": "1",
    }
    assert "series_id" not in first["extendedProperties"]["private"]


def test_w4_partial_failure_names_the_item_and_its_failed_occurrences():
    # Flyer: calls 1-5 are item 1, calls 6-9 are item 2.
    calendar = FakeCalendarClient(fail_calls={7, 8})
    kwargs = _propose(calendar=calendar)[4]
    ack = process_slack_message_event(_reply("yes"), **kwargs).reply_text
    assert ack.startswith("已確認，但 2/21 個活動未能建立：第 2 項（音樂班，4 次中 2 次未建立）。")
    assert "Accepted, but 2 of 21 events failed: item 2 (2 of its 4)" in ack
    assert "只會重試未完成嘅次數" in ack


def test_w5_second_yes_retries_only_the_missing_occurrences():
    calendar = FakeCalendarClient(fail_calls={7, 8})
    kwargs = _propose(calendar=calendar)[4]
    process_slack_message_event(_reply("yes"), **kwargs)
    first_round = list(calendar.calls)
    calendar.fail_calls = set()
    ack = process_slack_message_event(_reply("yes", 2), **kwargs).reply_text
    retried = calendar.calls[len(first_round):]
    assert [d.occurrence_date for d in retried] == [date(2026, 11, 10), date(2026, 11, 17)]
    assert "已確認。新建立 2 個活動，之前已建立 19 個，冇重複。" in ack


def test_w6_the_same_key_derives_the_same_google_id_across_retries():
    calendar = FakeCalendarClient(fail_calls={1})
    _, _, _, calendar, kwargs = _propose(calendar=calendar)
    process_slack_message_event(_reply("yes"), **kwargs)
    first_id = _draft_to_google_event(calendar.calls[0])["id"]
    calendar.fail_calls = set()
    process_slack_message_event(_reply("yes", 2), **kwargs)
    retry = [d for d in calendar.calls[FLYER_EVENTS:]]
    assert _draft_to_google_event(retry[0])["id"] == first_id


def test_w7_a_stored_count_that_no_longer_expands_refuses_the_whole_list():
    confirmation = _accepted([_stored_item(count=4)])  # really expands to 5
    calendar, audit = FakeCalendarClient(), InMemoryCalendarAuditStore()
    result = write_calendar_batch_create(
        confirmation, client=calendar, audit_store=audit, now=NOW,
    )
    assert (result.outcome, result.error_type) == (
        CalendarWriteOutcome.REFUSED, "series_count_changed")
    assert calendar.calls == []
    assert len(audit.list_all()) == 1


@pytest.mark.parametrize(("item", "error"), [
    (_stored_item(all_day=True), "series_all_day"),
    (_stored_item(spec=_spec(range_end=date(2028, 11, 30)), count=40), "series_over_cap"),
    (_stored_item(count=0), "series_count_changed"),
    (_stored_item(count=10_000), "series_count_changed"),
])
def test_w8_bad_stored_series_items_refuse_with_no_google_call(item, error):
    confirmation = _accepted([item])
    calendar = FakeCalendarClient()
    result = write_calendar_batch_create(confirmation, client=calendar, now=NOW)
    assert (result.outcome, result.error_type) == (CalendarWriteOutcome.REFUSED, error)
    assert calendar.calls == []


def test_w8b_every_item_dropped_refuses():
    confirmation = _accepted([_stored_item()], dropped=(1,))
    calendar = FakeCalendarClient()
    result = write_calendar_batch_create(confirmation, client=calendar, now=NOW)
    assert (result.outcome, result.error_type) == (
        CalendarWriteOutcome.REFUSED, "batch_all_dropped")
    assert calendar.calls == []


def test_w8c_remaining_items_over_the_event_cap_refuse():
    items = [
        _stored_item(index=n, title=f"第 {n} 班",
                     spec=_spec(weekdays=(0, 1, 2, 3, 4), range_start=date(2026, 11, 2),
                                range_end=date(2026, 11, 27)),
                     count=20)
        for n in range(1, 5)
    ]
    confirmation = _accepted(items)
    calendar = FakeCalendarClient()
    result = write_calendar_batch_create(confirmation, client=calendar, now=NOW)
    assert (result.outcome, result.error_type) == (
        CalendarWriteOutcome.REFUSED, "batch_over_event_cap")
    assert calendar.calls == []


def test_w9_the_other_writers_still_refuse_a_list_confirmation():
    confirmation = _accepted([_stored_item()])
    calendar = FakeCalendarClient()
    series = write_calendar_series_create(confirmation, client=calendar, now=NOW)
    single = write_calendar_create(confirmation, client=calendar, now=NOW)
    assert series.outcome == CalendarWriteOutcome.REFUSED
    assert single.outcome == CalendarWriteOutcome.REFUSED
    assert calendar.calls == []


def test_w10_google_auth_error_mid_list_keeps_honest_counts():
    class Expired(RuntimeError):
        pass

    calendar = FakeCalendarClient(fail_with=Expired("invalid_grant: token expired"))
    _, _, _, calendar, kwargs = _propose(calendar=calendar)
    ack = process_slack_message_event(_reply("yes"), **kwargs).reply_text
    assert ack.startswith("已確認，但 21/21 個活動未能建立（Google 登入已過期）")
    assert "（新建立 0 個，之前已有 0 個）" in ack


# --- C: the store --------------------------------------------------------------


def test_c1_json_round_trip_keeps_every_series_item(tmp_path):
    store = JsonDirConfirmationStore(tmp_path)
    _, _, _, kwargs = _setup()
    kwargs["confirmation_store"] = store
    process_slack_message_event(_event(FLYER_TEXT), **kwargs)
    (conf,) = store.list_all()
    reloaded = JsonDirConfirmationStore(tmp_path).get(conf.confirmation_id)
    assert reloaded is not None
    assert reloaded.parse_result.batch == conf.parse_result.batch
    assert reloaded.parse_result.batch[0].series == _spec()
    assert [i.occurrence_count for i in reloaded.parse_result.batch] == [5, 4, 4, 4, 4]


def test_c2_a_pre_33c_confirmation_file_loads_unchanged(tmp_path):
    store = JsonDirConfirmationStore(tmp_path)
    _, _, _, kwargs = _setup(_payload([
        _item("急救課程", day="2026-11-06", start="09:00", source="11月6日 9:00 急救課程"),
        _item("考試", day="2026-11-13", start="09:00", source="11月13日 9:00 考試"),
    ]))
    kwargs["confirmation_store"] = store
    process_slack_message_event(_event("11月6日 9:00 急救課程\n11月13日 9:00 考試"), **kwargs)
    (path,) = list(tmp_path.glob("*.json"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    for item in raw["parse_result"]["batch"]:
        del item["series"]
        del item["occurrence_count"]
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    (conf,) = JsonDirConfirmationStore(tmp_path).list_all()
    assert [i.series for i in conf.parse_result.batch] == [None, None]
    assert [i.occurrence_count for i in conf.parse_result.batch] == [1, 1]
    calendar = FakeCalendarClient()
    accepted = resolve_confirmation(
        conf.confirmation_id, "accept", store=JsonDirConfirmationStore(tmp_path), now=NOW,
    )
    result = write_calendar_batch_create(accepted, client=calendar, now=NOW)
    assert result.outcome == CalendarWriteOutcome.SUCCESS
    assert [d.confirmation_id for d in calendar.calls] == [
        batch_child_key(conf.confirmation_id, 1), batch_child_key(conf.confirmation_id, 2),
    ]


def test_c3_the_same_slack_message_twice_reuses_one_confirmation():
    _, store, _, calendar, kwargs = _propose()
    again = process_slack_message_event(_event(FLYER_TEXT), **kwargs)
    assert len(store.list_all()) == 1
    (conf,) = store.list_all()
    assert again.reply_text == conf.proposal_text
    assert calendar.calls == []


# --- O: overlap ----------------------------------------------------------------


def _listed(day: str, start: str, end: str, summary="鋼琴課") -> CalendarListedEvent:
    return CalendarListedEvent(
        event_id=f"evt-{day}-{start}", summary=summary,
        start=datetime.fromisoformat(f"{day}T{start}:00+08:00"),
        end=datetime.fromisoformat(f"{day}T{end}:00+08:00"), all_day=False,
    )


def test_o1_one_union_list_covers_every_occurrence_and_collapses_per_item():
    existing = [_listed(day, "15:30", "16:30")
                for day in ("2026-11-02", "2026-11-09", "2026-11-16",
                            "2026-11-23", "2026-11-30")]
    calendar = FakeCalendarClient(listed_events=existing)
    reply, _, _, calendar, _ = _propose(calendar=calendar)
    assert len(calendar.list_calls) == 1
    _, time_min, time_max = calendar.list_calls[0]
    assert time_min == datetime(2026, 11, 2, 15, 15, tzinfo=TZ)
    assert time_max == datetime(2026, 11, 30, 16, 15, tzinfo=TZ)
    assert "第 1 項 5 次中 5 次撞期（例如 2026-11-02 15:30–16:30 鋼琴課）/ " \
           "item 1: 5 of its 5 events overlap" in reply
    assert reply.count("第 1 項 5 次中") == 1


def test_o2_an_event_only_in_the_last_week_is_still_found():
    calendar = FakeCalendarClient(listed_events=[_listed("2026-11-30", "15:30", "16:30")])
    reply, _, _, _, _ = _propose(calendar=calendar)
    assert "第 1 項 5 次中 1 次撞期（例如 2026-11-30" in reply


def test_o3_two_repeating_items_sharing_a_weekday_warn_once():
    payload = _payload([
        _item("游水班", source="游水班（逢一三五）", series=_series(["MO", "WE", "FR"])),
        _class_item("美術班", "MO", "美術班（星期一）"),
    ])
    reply, store, _, _, _ = _propose(payload, text="游水班（逢一三五）\n美術班（星期一）")
    assert reply.count("注意：第 1 同第 2 項時間重疊 / "
                       "Warning: items 1 and 2 overlap each other.") == 1
    (conf,) = store.list_all()
    assert len(conf.parse_result.batch) == 2


def test_o4_five_different_weekday_items_make_no_between_item_warning():
    reply, _, _, _, _ = _propose()
    assert "時間重疊" not in reply


def test_o5_a_failed_list_still_allows_yes():
    calendar = FakeCalendarClient(fail_list_with=RuntimeError("synthetic list failure"))
    reply, _, _, calendar, kwargs = _propose(calendar=calendar)
    assert "撞期" in reply or "could not" in reply.lower()
    ack = process_slack_message_event(_reply("yes"), **kwargs)
    assert ack.reply_text.startswith("已確認。已建立 21 個活動")


# --- pure helpers, logs and isolation ------------------------------------------


def test_expand_item_is_a_pure_cut_at_the_shown_first_occurrence():
    item = _stored_item(count=3, start=datetime(2026, 11, 16, 15, 15, tzinfo=TZ),
                        end=datetime(2026, 11, 16, 16, 15, tzinfo=TZ))
    assert [o.local_date for o in expand_item(item)] == [
        date(2026, 11, 16), date(2026, 11, 23), date(2026, 11, 30),
    ]
    assert expand_item(item) == expand_item(item)


def test_expand_batch_items_flattens_every_window_once():
    _, store, _, _, _ = _propose()
    (conf,) = store.list_all()
    flat = expand_batch_items(conf.parse_result.batch)
    assert len(flat) == FLYER_EVENTS
    assert flat[0].item_index == 1 and flat[0].local_date == date(2026, 11, 2)
    assert {o.item_index for o in flat} == {1, 2, 3, 4, 5}


def test_item_occurrences_degrades_for_a_broken_stored_spec():
    item = _stored_item(spec=_spec(range_end=date(2026, 1, 1)), count=5)
    assert len(item_occurrences(item)) == 1


def test_g1_boundary_logs_carry_counts_but_no_titles_or_dates(monkeypatch):
    from cec_vivisystem import event_batch

    records: list[tuple[str, dict]] = []
    real = event_batch.logger.info
    monkeypatch.setattr(
        event_batch.logger, "info",
        lambda event, **kw: (records.append((event, kw)), real(event, **kw))[1],
    )
    _propose()
    (_, fields) = next(r for r in records if r[0] == "batch_extract_completed")
    assert (fields["series_item_count"], fields["series_event_count"],
            fields["total_event_count"]) == (5, 21, 21)
    for value in fields.values():
        assert "美術班" != value


def test_i1_the_medic_path_still_loads_no_series_module():
    import subprocess
    import sys

    code = (
        "import sys, cec_vivisystem.medic_librarian.answer as a;"
        "print('cec_vivisystem.series' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)
    assert out.stdout.strip() == "False"


@pytest.mark.parametrize("series", ["逢星期一", ["MO"], {"weekdays": "MO"},
                                    {"weekdays": ["MO"], "range_start": "nope",
                                     "range_end": "2026-11-30", "start_time": "15:15",
                                     "end_time": None}])
def test_x1_garbage_series_values_never_crash(series):
    payload = _payload([
        _item("A 班", source="A 班", series=series),
        _item("考試", day="2026-11-13", start="09:00", source="11月13日 9:00 考試"),
    ])
    reply, store, _, calendar, _ = _propose(payload, text="A 班\n11月13日 9:00 考試")
    assert reply  # a reply, never an exception
    assert calendar.calls == []
    for conf in store.list_all():
        for item in conf.parse_result.batch or ():
            assert item.title != "A 班" or item.series is not None


def test_x1b_a_very_long_series_title_is_clipped():
    payload = _payload([
        _item("班" * 400, source="班", series=_series(["MO"])),
        _class_item("音樂班", "TU", "音樂班（星期二）"),
    ])
    _, store, _, _, _ = _propose(payload, text="班\n音樂班（星期二）")
    (conf,) = store.list_all()
    assert all(len(i.title) <= 120 for i in conf.parse_result.batch)


def test_x3_prompt_files_carry_the_new_rules_and_keep_the_rollbacks():
    from cec_vivisystem.parse_fallback import PROMPT_PATHS

    text = PROMPT_PATHS["v7"].read_text(encoding="utf-8")
    image = (PROMPT_PATHS["v7"].parent / "create_event_image.v2.txt").read_text(
        encoding="utf-8")
    for prompt in (text, image):
        flat = " ".join(prompt.split())
        assert "no activity name of its own is not an item" in flat
        assert "Copy that shared date range" in flat
        assert "One class line is one item" in flat
        assert "never compute a weekday" in flat
        assert not contains_simplified_markers(prompt)
    assert PROMPT_PATHS["v6"].exists() and PROMPT_PATHS["v5"].exists()
    assert "Repeating items inside a list" in text


def test_reason_copy_covers_every_new_reason():
    for reason in ("series_open_ended", "series_over_cap", "series_no_occurrences"):
        zh, en = REASON_COPY[reason]
        assert zh and en and not contains_simplified_markers(zh)
