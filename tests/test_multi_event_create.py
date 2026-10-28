"""Phase 33a multi-event list create (ADR 0018). Fake model/calendar; fixed HKT clock."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_writer import (
    FakeCalendarClient,
    InMemoryCalendarAuditStore,
    _draft_to_google_event,
    batch_child_key,
    write_calendar_batch_create,
    write_calendar_create,
    write_calendar_series_create,
)
from cec_vivisystem.confirmation import (
    InMemoryConfirmationStore,
    JsonDirConfirmationStore,
    create_confirmation,
    resolve_confirmation,
)
from cec_vivisystem.event_batch import (
    OFFLINE_LIST_REPLY,
    BatchEdit,
    classify_batch_edit,
    looks_like_dated_list,
)
from cec_vivisystem.listener import process_slack_message_event
from cec_vivisystem.models import (
    CalendarWriteOutcome,
    ConfirmationStatus,
    IntentType,
)
from cec_vivisystem.parse_fallback import (
    CREATE_SCHEMA,
    CREATE_SCHEMA_V6,
    DEFAULT_PROMPT_VERSION,
    FakePayloadLlmParser,
    XaiChatLlmParser,
    load_live_llm_parsers,
    load_prompt_version,
    parse_with_fallback,
    validate_create_payload,
)
from cec_vivisystem.parser import contains_simplified_markers

TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 28, 9, 0, tzinfo=TZ)  # Wednesday
CHANNEL = "C_PLANS"
TS = "1791000000.000100"

FIVE_LINES = [
    ("急救課程 Day 1", "2026-11-06", "09:00", "17:00", "11月6日 9:00-17:00 急救課程 Day 1"),
    ("急救課程 Day 2", "2026-11-13", "09:00", "17:00", "11月13日 9:00-17:00 急救課程 Day 2"),
    ("急救考試", "2026-11-20", "09:00", None, "11月20日 9:00 急救考試"),
    ("複習", "2026-11-27", "14:00", "17:00", "11月27日 14:00-17:00 複習"),
    ("實習", "2026-12-04", "10:00", "12:00", "12月4日 10:00-12:00 實習"),
]
FIVE_TEXT = "培訓行程：\n" + "\n".join(line[-1] for line in FIVE_LINES)


def _item(title, day, start=None, end=None, source=None, **overrides) -> dict:
    item = {
        "title": title,
        "start": f"{day}T{start}:00+08:00" if start else None,
        "end": f"{day}T{end}:00+08:00" if end else None,
        "all_day": False,
        "location": None,
        "participants": [],
        "missing_fields": [] if start else ["time"],
        "series": None,
        "date": day,
        "stated_weekday": None,
        "source_text": source,
    }
    item.update(overrides)
    return item


def _payload(items: list[dict], *, over_cap: bool = False, intent="create_event") -> dict:
    return {"intent_type": intent, "events": items, "over_cap": over_cap, "confidence": "medium"}


def _five_payload() -> dict:
    return _payload([_item(*line) for line in FIVE_LINES])


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


def _kwargs(store, calendar, audit, llm=None, **extra) -> dict:
    return {
        "allowed_channel_ids": {CHANNEL},
        "confirmation_store": store,
        "calendar_client": calendar,
        "calendar_audit_store": audit,
        "llm_parser": llm,
        "now": NOW,
        **extra,
    }


def _setup(payload=None, calendar=None):
    store, audit = InMemoryConfirmationStore(), InMemoryCalendarAuditStore()
    calendar = calendar or FakeCalendarClient()
    llm = FakePayloadLlmParser(payload or _five_payload())
    return store, audit, calendar, _kwargs(store, calendar, audit, llm)


# --- one Slack message → one list proposal -------------------------------------


def test_five_line_paste_gives_one_proposal_with_five_items_and_no_write():
    store, _, calendar, kwargs = _setup()
    result = process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    text = result.reply_text
    assert text.startswith("請確認以下 5 個活動")
    for number, line in enumerate(FIVE_LINES, start=1):
        assert f"{number}. {line[1]}" in text
        assert line[0] in text
    assert "3. 2026-11-20（五）09:00–10:00 急救考試（冇講結束時間，預設 1 小時" in text
    assert "No calendar change will be made until you confirm." in text
    assert not contains_simplified_markers(text)
    assert calendar.calls == []  # header did not become a Calendar list either
    (conf,) = store.list_all()
    assert conf.status == ConfirmationStatus.PENDING
    assert len(conf.parse_result.batch) == 5
    assert conf.parse_result.title == "急救課程 Day 1"  # mirrors item 1


def test_yes_creates_every_item_with_stable_keys_and_one_audit_row_each():
    store, audit, calendar, kwargs = _setup()
    process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    ack = process_slack_message_event(_reply("yes"), **kwargs)
    assert ack.reply_text.startswith("已確認。已建立 5 個活動")
    (conf,) = store.list_all()
    keys = [batch_child_key(conf.confirmation_id, n) for n in range(1, 6)]
    assert keys[0].endswith("#item-01")
    assert [d.confirmation_id for d in calendar.calls] == keys
    body = _draft_to_google_event(calendar.calls[2])
    assert body["id"] == hashlib.sha256(("cec-confirmation:" + keys[2]).encode()).hexdigest()
    assert body["extendedProperties"]["private"] == {
        "confirmation_id": keys[2],
        "parent_confirmation_id": conf.confirmation_id,
        "batch_index": "3",
    }
    assert body["end"]["dateTime"] == "2026-11-20T10:00:00+08:00"  # proposed default end
    rows = audit.list_all()
    assert [r.confirmation_id for r in rows] == keys
    assert {r.op for r in rows} == {"create"}


def test_one_line_list_without_newlines_still_proposes_two_items():
    text = "星期一 3點游水，星期三 4點鋼琴"
    payload = _payload([
        _item("游水", "2026-11-02", "15:00", source="星期一 3點游水"),
        _item("鋼琴", "2026-11-04", "16:00", source="星期三 4點鋼琴"),
    ])
    store, _, _, kwargs = _setup(payload)
    result = process_slack_message_event(_event(text), **kwargs)
    assert result.reply_text.startswith("請確認以下 2 個活動")
    assert len(store.list_all()[0].parse_result.batch) == 2


# --- unusable lines are skipped and listed ---------------------------------------


def test_unusable_lines_are_listed_not_included_and_never_written():
    text = (
        "10月20日 10:00 舊活動\n"
        "11月6日 9:00 急救課程\n"
        "11月9日（五）9:00 錯星期\n"
        "11月13日 9:00 考試\n"
        "11月14日 23:00-01:00 夜更"
    )
    payload = _payload([
        _item("舊活動", "2026-10-20", "10:00", source="10月20日 10:00 舊活動"),
        _item("急救課程", "2026-11-06", "09:00", source="11月6日 9:00 急救課程"),
        _item("錯星期", "2026-11-09", "09:00", source="11月9日（五）9:00 錯星期",
              stated_weekday="FR"),
        _item("考試", "2026-11-13", "09:00", source="11月13日 9:00 考試"),
        _item("夜更", "2026-11-14", "23:00", source="11月14日 23:00-01:00 夜更",
              end="2026-11-15T01:00:00+08:00"),
    ])
    payload["events"][4]["end"] = "2026-11-15T01:00:00+08:00"
    store, _, calendar, kwargs = _setup(payload)
    proposal = process_slack_message_event(_event(text), **kwargs).reply_text
    assert proposal.startswith("請確認以下 2 個活動")
    assert "未包括 / Not included (3):" in proposal
    assert "「10月20日 10:00 舊活動」— 日期已過 / date has passed" in proposal
    assert "星期幾同日期唔夾 / weekday does not match the date" in proposal
    assert "時間跨日 / time crosses midnight" in proposal
    # Original numbers are kept, so items 2 and 4 are the included ones.
    assert "2. 2026-11-06" in proposal and "4. 2026-11-13" in proposal
    process_slack_message_event(_reply("yes"), **kwargs)
    (conf,) = store.list_all()
    assert [d.confirmation_id for d in calendar.calls] == [
        batch_child_key(conf.confirmation_id, 2), batch_child_key(conf.confirmation_id, 4),
    ]


def test_no_usable_line_clarifies_without_confirmation():
    payload = _payload([
        _item("舊活動", "2026-10-20", "10:00", source="10月20日 10:00 舊活動"),
        _item("又舊", "2026-10-21", "10:00", source="10月21日 10:00 又舊"),
    ])
    store, _, calendar, kwargs = _setup(payload)
    text = "10月20日 10:00 舊活動\n10月21日 10:00 又舊"
    reply = process_slack_message_event(_event(text), **kwargs).reply_text
    assert reply.startswith("呢個訊息入面冇一項可以加入日曆")
    assert reply.count("日期已過") == 2
    assert store.list_all() == [] and calendar.calls == []


# --- date with no time → time question ------------------------------------------


def test_date_only_line_asks_for_the_time_and_makes_no_proposal():
    text = "11月6日 9:00 急救課程\n11月13日 結業禮"
    payload = _payload([
        _item("急救課程", "2026-11-06", "09:00", source="11月6日 9:00 急救課程"),
        _item("結業禮", "2026-11-13", source="11月13日 結業禮"),
    ])
    store, _, calendar, kwargs = _setup(payload)
    reply = process_slack_message_event(_event(text), **kwargs).reply_text
    assert reply.startswith("以下項目有日期但冇時間")
    assert "「11月13日 結業禮」" in reply
    assert "我未建立任何提案。/ No proposal was made." in reply
    assert "全日" not in reply.split("\n", 2)[2]  # never offered as an all-day event
    assert store.list_all() == []
    later = process_slack_message_event(_reply("yes"), **kwargs)
    assert later.reply_text is None or "已建立" not in later.reply_text
    assert calendar.calls == []


def test_explicit_all_day_line_is_included_not_asked():
    text = "11月6日 9:00 急救課程\n12月4日 全日 實習"
    payload = _payload([
        _item("急救課程", "2026-11-06", "09:00", source="11月6日 9:00 急救課程"),
        _item("實習", "2026-12-04", "00:00", end=None, source="12月4日 全日 實習",
              all_day=True, missing_fields=[]),
    ])
    store, _, _, kwargs = _setup(payload)
    reply = process_slack_message_event(_event(text), **kwargs).reply_text
    assert "2. 2026-12-04（五）全日 實習" in reply
    assert len(store.list_all()) == 1


def test_model_all_day_without_cue_is_treated_as_missing_time():
    text = "11月6日 9:00 急救課程\n11月13日 結業禮"
    payload = _payload([
        _item("急救課程", "2026-11-06", "09:00", source="11月6日 9:00 急救課程"),
        _item("結業禮", "2026-11-13", "00:00", source="11月13日 結業禮",
              all_day=True, missing_fields=[]),
    ])
    store, _, _, kwargs = _setup(payload)
    reply = process_slack_message_event(_event(text), **kwargs).reply_text
    assert reply.startswith("以下項目有日期但冇時間")
    assert store.list_all() == []


# --- deselect before yes ---------------------------------------------------------


def test_drop_one_item_reposts_list_still_pending_then_yes_skips_it():
    store, _, calendar, kwargs = _setup()
    process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    edited = process_slack_message_event(_reply("唔要 3"), **kwargs).reply_text
    assert edited.startswith("請確認以下 4 個活動")
    assert "已剔走 / Removed: 第 3 項" in edited
    assert "急救考試" not in edited
    assert "4. 2026-11-27" in edited  # never renumbered
    (conf,) = store.list_all()
    assert conf.status == ConfirmationStatus.PENDING
    assert conf.batch_dropped == [3]
    assert conf.proposal_text == edited
    assert calendar.calls == []
    ack = process_slack_message_event(_reply("yes", 2), **kwargs)
    assert ack.reply_text.startswith("已確認。已建立 4 個活動")
    assert [d.batch_index for d in calendar.calls] == [1, 2, 4, 5]


def test_keep_only_items_then_yes_creates_exactly_those():
    _, _, calendar, kwargs = _setup()
    process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    edited = process_slack_message_event(_reply("只要 1,4"), **kwargs).reply_text
    assert edited.startswith("請確認以下 2 個活動")
    assert "已剔走 / Removed: 第 2、3、5 項" in edited
    assert calendar.calls == []
    process_slack_message_event(_reply("yes", 2), **kwargs)
    assert [d.batch_index for d in calendar.calls] == [1, 4]


def test_english_drop_and_successive_edits_accumulate():
    store, _, calendar, kwargs = _setup()
    process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    process_slack_message_event(_reply("drop 2"), **kwargs)
    process_slack_message_event(_reply("keep 1, 5", 2), **kwargs)
    (conf,) = store.list_all()
    assert conf.batch_dropped == [2, 3, 4]
    process_slack_message_event(_reply("yes", 3), **kwargs)
    assert [d.batch_index for d in calendar.calls] == [1, 5]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("唔要 3", BatchEdit(False, (3,))),
        ("剔走 2,4", BatchEdit(False, (2, 4))),
        ("drop 3", BatchEdit(False, (3,))),
        ("唔要第2個", BatchEdit(False, (2,))),
        ("剔除 2、4", BatchEdit(False, (2, 4))),
        ("remove 2 4", BatchEdit(False, (2, 4))),
        ("只要 1,4", BatchEdit(True, (1, 4))),
        ("只要１，４", BatchEdit(True, (1, 4))),  # full-width
        ("keep 1,4", BatchEdit(True, (1, 4))),
        ("淨係要 12", BatchEdit(True, (12,))),
    ],
)
def test_narrowing_reply_variants(reply, expected):
    assert classify_batch_edit(reply) == expected


@pytest.mark.parametrize("reply", ["不要", "yes", "唔要", "drop it", "keep calm", "2", ""])
def test_non_narrowing_replies_are_not_edits(reply):
    assert classify_batch_edit(reply) is None


def test_bad_edits_leave_state_unchanged():
    store, _, calendar, kwargs = _setup()
    process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    unknown = process_slack_message_event(_reply("唔要 9"), **kwargs).reply_text
    assert "清單入面冇第 9 項" in unknown
    every = process_slack_message_event(_reply("唔要 1,2,3,4,5", 2), **kwargs).reply_text
    assert "至少要保留 1 項" in every
    bare = process_slack_message_event(_reply("2", 3), **kwargs).reply_text
    assert "唔要 2" in bare and "No calendar change" in bare
    (conf,) = store.list_all()
    assert conf.batch_dropped == [] and conf.status == ConfirmationStatus.PENDING
    assert calendar.calls == []


# --- the Calendar yes gate stays -------------------------------------------------


def test_no_rejects_the_whole_list_and_nothing_is_written():
    store, audit, calendar, kwargs = _setup()
    process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    process_slack_message_event(_reply("不要"), **kwargs)
    (conf,) = store.list_all()
    assert conf.status == ConfirmationStatus.REJECTED
    after = process_slack_message_event(_reply("唔要 2", 2), **kwargs).reply_text
    assert "已經取消" in after
    process_slack_message_event(_reply("yes", 3), **kwargs)
    assert calendar.calls == [] and audit.list_all() == []


def test_batch_writer_refuses_pending_confirmation_without_google_call():
    store, audit = InMemoryConfirmationStore(), InMemoryCalendarAuditStore()
    result = parse_with_fallback(FIVE_TEXT, now=NOW, llm=FakePayloadLlmParser(_five_payload()),
                                 llm_first=True)
    pending = create_confirmation(result, store=store, now=NOW, channel_id=CHANNEL,
                                  thread_ts="t1", source_message_id="t1")
    calendar = FakeCalendarClient()
    refused = write_calendar_batch_create(pending, client=calendar, audit_store=audit)
    assert refused.outcome == CalendarWriteOutcome.REFUSED
    assert refused.error_type == "not_accepted"
    assert calendar.calls == []


def test_single_and_series_writers_refuse_list_confirmations():
    store = InMemoryConfirmationStore()
    result = parse_with_fallback(FIVE_TEXT, now=NOW, llm=FakePayloadLlmParser(_five_payload()),
                                 llm_first=True)
    pending = create_confirmation(result, store=store, now=NOW, channel_id=CHANNEL,
                                  thread_ts="t1", source_message_id="t1")
    accepted = resolve_confirmation(pending.confirmation_id, "accept", store=store, now=NOW)
    calendar = FakeCalendarClient()
    single = write_calendar_create(accepted, client=calendar)
    series = write_calendar_series_create(accepted, client=calendar)
    assert single.outcome == CalendarWriteOutcome.REFUSED
    assert series.outcome == CalendarWriteOutcome.REFUSED
    assert calendar.calls == []


def test_edit_after_acceptance_changes_nothing():
    _, _, calendar, kwargs = _setup()
    process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    process_slack_message_event(_reply("yes"), **kwargs)
    reply = process_slack_message_event(_reply("唔要 2", 2), **kwargs).reply_text
    assert "已經確認咗" in reply
    assert len(calendar.calls) == 5


def test_partial_failure_reports_counts_and_second_yes_retries_only_missing():
    store, audit = InMemoryConfirmationStore(), InMemoryCalendarAuditStore()
    llm = FakePayloadLlmParser(_five_payload())
    failing = FakeCalendarClient(fail_calls={2})
    process_slack_message_event(_event(FIVE_TEXT), **_kwargs(store, failing, audit, llm))
    ack = process_slack_message_event(_reply("yes"), **_kwargs(store, failing, audit, llm))
    assert len(failing.calls) == 5  # loop continued past the failure
    assert "1/5 個活動未能建立" in ack.reply_text
    assert "第 2 項（2026-11-13 急救課程 Day 2）" in ack.reply_text
    assert "新建立 4 個" in ack.reply_text
    retry_calendar = FakeCalendarClient()
    again = process_slack_message_event(
        _reply("yes", 2), **_kwargs(store, retry_calendar, audit, llm)
    )
    assert [d.batch_index for d in retry_calendar.calls] == [2]
    assert retry_calendar.calls[0].confirmation_id == failing.calls[1].confirmation_id
    assert "新建立 1 個活動，之前已建立 4 個" in again.reply_text


# --- routing guard, cap, offline, single-item regression -------------------------


def test_list_header_reaches_model_not_calendar_list_route():
    result = parse_with_fallback(FIVE_TEXT, now=NOW, llm=FakePayloadLlmParser(_five_payload()),
                                 llm_first=True)
    assert result.intent_type == IntentType.CREATE_EVENT
    assert result.batch is not None and len(result.batch) == 5


def test_clock_less_exam_lines_never_become_an_important_date():
    text = "11月6日 中文考試\n11月13日 英文考試"
    llm = FakePayloadLlmParser(_payload([
        _item("中文考試", "2026-11-06", source="11月6日 中文考試"),
        _item("英文考試", "2026-11-13", source="11月13日 英文考試"),
    ]))
    result = parse_with_fallback(text, now=NOW, llm=llm, llm_first=True)
    assert llm.calls == [text]
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert result.missing_fields == ["time"]


def test_single_line_list_query_route_is_unchanged():
    llm = FakePayloadLlmParser(_five_payload())
    result = parse_with_fallback("聽日有咩活動", now=NOW, llm=llm, llm_first=True)
    assert result.intent_type == IntentType.LIST_EVENTS
    assert llm.calls == []


def test_over_cap_dated_lines_clarify_before_any_model_call():
    text = "\n".join(f"11月{d}日 9:00 活動{d}" for d in range(1, 17))
    store, _, calendar, kwargs = _setup()
    reply = process_slack_message_event(_event(text), **kwargs).reply_text
    assert "一次最多加 15 個活動" in reply
    assert kwargs["llm_parser"].calls == []
    assert store.list_all() == [] and calendar.calls == []


def test_model_over_cap_flag_clarifies_and_never_truncates():
    store, _, _, kwargs = _setup(_payload([], over_cap=True))
    reply = process_slack_message_event(_event("好多活動\n11月6日\n11月7日"), **kwargs)
    assert "一次最多加 15 個活動" in reply.reply_text
    assert store.list_all() == []


def test_offline_mode_dated_list_asks_to_send_one_per_message():
    store, audit = InMemoryConfirmationStore(), InMemoryCalendarAuditStore()
    calendar = FakeCalendarClient()
    reply = process_slack_message_event(
        _event(FIVE_TEXT), **_kwargs(store, calendar, audit, None)
    ).reply_text
    assert reply == OFFLINE_LIST_REPLY
    assert store.list_all() == [] and calendar.calls == []


def test_one_v6_item_keeps_the_single_event_path():
    text = "11月6日 9:00 急救課程"
    payload = _payload([_item("急救課程", "2026-11-06", "09:00", source=text)])
    store, _, calendar, kwargs = _setup(payload)
    reply = process_slack_message_event(_event(text), **kwargs).reply_text
    (conf,) = store.list_all()
    assert conf.parse_result.batch is None
    assert not reply.startswith("請確認以下")
    process_slack_message_event(_reply("yes"), **kwargs)
    assert [d.confirmation_id for d in calendar.calls] == [conf.confirmation_id]
    assert calendar.calls[0].batch_index is None


def test_dated_list_detector():
    assert looks_like_dated_list(FIVE_TEXT)
    assert not looks_like_dated_list("11月6日 9:00 急救課程")
    assert not looks_like_dated_list("買餸\n執屋")


# --- store compatibility ---------------------------------------------------------


def test_list_confirmation_round_trips_through_json_store(tmp_path):
    store = JsonDirConfirmationStore(tmp_path)
    kwargs = _kwargs(store, FakeCalendarClient(), InMemoryCalendarAuditStore(),
                     FakePayloadLlmParser(_five_payload()))
    process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    process_slack_message_event(_reply("唔要 3"), **kwargs)
    (before,) = store.list_all()
    (after,) = JsonDirConfirmationStore(tmp_path).list_all()
    assert after.parse_result.batch == before.parse_result.batch
    assert after.batch_dropped == [3]
    assert after.proposal_text == before.proposal_text


def test_pre_33a_confirmation_file_loads_without_batch(tmp_path):
    store = JsonDirConfirmationStore(tmp_path)
    single = parse_with_fallback(
        "11月6日 9:00 急救課程", now=NOW, llm_first=True,
        llm=FakePayloadLlmParser(_payload([_item("急救課程", "2026-11-06", "09:00")])),
    )
    conf = create_confirmation(single, store=store, now=NOW, channel_id=CHANNEL,
                               thread_ts="t1", source_message_id="t1")
    (path,) = tmp_path.glob("*.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("batch_dropped", "batch_overlap_notes", "batch_overlap_warning"):
        data.pop(key, None)
    for key in ("batch", "batch_not_included"):
        data["parse_result"].pop(key, None)
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = JsonDirConfirmationStore(tmp_path).get(conf.confirmation_id)
    assert loaded.parse_result.batch is None
    assert loaded.parse_result.batch_not_included == ()
    assert loaded.batch_dropped == []
    assert loaded.parse_result.title == "急救課程"


# --- prompt version and provider contract ----------------------------------------


def test_v5_stays_default_and_v6_is_opt_in():
    assert DEFAULT_PROMPT_VERSION == "v5"
    assert load_prompt_version({}) == "v5"
    assert load_prompt_version({"CEC_CREATE_PROMPT_VERSION": "V6"}) == "v6"
    assert load_prompt_version({"CEC_CREATE_PROMPT_VERSION": "v9"}) == "v5"
    primary, _ = load_live_llm_parsers(
        {"XAI_API_KEY": "fake-key", "CEC_CREATE_PROMPT_VERSION": "v6"}
    )
    assert primary is not None and primary._prompt_key == "v6"


def _provider(monkeypatch, response, finish_reason="stop"):
    create = Mock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(response)), finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
    ))
    monkeypatch.setattr("openai.OpenAI", Mock(return_value=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))))
    return XaiChatLlmParser(api_key="fake-key", model="fake-model", prompt_version="v6"), create


def test_v6_request_uses_events_schema_and_prompt(monkeypatch):
    llm, create = _provider(monkeypatch, _five_payload())
    result = parse_with_fallback(FIVE_TEXT, now=NOW, llm=llm, llm_first=True)
    assert len(result.batch) == 5
    request = create.call_args.kwargs
    schema = request["response_format"]["json_schema"]["schema"]
    assert schema is CREATE_SCHEMA_V6
    assert schema["properties"]["events"]["maxItems"] == 15
    assert "series" in schema["properties"]["events"]["items"]["required"]
    assert request["max_tokens"] == 2048
    assert "Lists:" in request["messages"][0]["content"]
    assert llm.prompt_version == "create_event.v6.txt"


def test_v6_cut_off_list_asks_to_split_not_unavailable(monkeypatch):
    llm, _ = _provider(monkeypatch, _five_payload(), finish_reason="length")
    result = parse_with_fallback(FIVE_TEXT, now=NOW, llm=llm, llm_first=True)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert "batch_too_long" in result.notes


def test_v5_request_shape_is_unchanged_by_default(monkeypatch):
    create = Mock()
    monkeypatch.setattr("openai.OpenAI", Mock(return_value=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))))
    llm = XaiChatLlmParser(api_key="fake-key", model="fake-model")
    create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "intent_type": "create_event", "title": "游水",
            "start": "2026-11-02T15:00:00+08:00", "end": None, "all_day": False,
            "location": None, "participants": [], "missing_fields": [],
            "confidence": "medium", "series": None,
        })))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
    result = parse_with_fallback("星期一 3點游水", now=NOW, llm=llm, llm_first=True)
    assert result.batch is None
    request = create.call_args.kwargs
    assert request["response_format"]["json_schema"]["schema"] is CREATE_SCHEMA
    assert request["max_tokens"] == 1024


@pytest.mark.parametrize(
    "payload",
    [
        {"intent_type": "create_event", "events": "x", "over_cap": False,
         "confidence": "medium"},
        {"intent_type": "create_event", "events": [{"title": "x"}], "over_cap": False,
         "confidence": "medium"},
        {"intent_type": "create_event", "events": [], "over_cap": "no",
         "confidence": "medium"},
        {"intent_type": "create_event", "events": [], "over_cap": False,
         "confidence": "medium", "extra": 1},
        _payload([_item("x", "Nov 6", "09:00")]),
        _payload([_item("x", "2026-11-06", "09:00", stated_weekday="XX")]),
    ],
)
def test_v6_payload_validation_rejects_garbage(payload):
    with pytest.raises((ValueError, TypeError)):
        validate_create_payload(payload)


def test_garbage_v6_payload_fails_visibly_without_proposal():
    store, _, calendar, kwargs = _setup({"intent_type": "create_event", "events": "x",
                                         "over_cap": False, "confidence": "medium"})
    result = process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    assert result.reply_text and "請確認" not in result.reply_text
    assert store.list_all() == [] and calendar.calls == []


def test_items_never_carry_series_id():
    _, _, calendar, kwargs = _setup()
    process_slack_message_event(_event(FIVE_TEXT), **kwargs)
    process_slack_message_event(_reply("yes"), **kwargs)
    assert all(d.series_id is None for d in calendar.calls)
    assert all(d.occurrence_date is None for d in calendar.calls)
    assert calendar.calls[0].start.date() == date(2026, 11, 6)
