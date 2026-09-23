"""Phase 23 synthetic regressions; provider responses are scripted, not eval scores."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_writer import FakeCalendarClient
from cec_vivisystem.confirmation import InMemoryConfirmationStore
from cec_vivisystem.listener import process_slack_message_event
from cec_vivisystem.models import IntentType
from cec_vivisystem.parse_fallback import XaiChatLlmParser, parse_with_fallback
from cec_vivisystem.parser import parse

TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 5, 9, tzinfo=TZ)
MESSAGE = "加個event，星期二9pm，Coco影相+散步"


@pytest.mark.parametrize(
    ("clock", "hour", "minute"),
    [
        ("9pm", 21, 0),
        ("9:15pm", 21, 15),
        ("9：15PM", 21, 15),
        ("12am", 0, 0),
        ("12pm", 12, 0),
        ("9am", 9, 0),
    ],
)
def test_cjk_adjacent_ampm(clock, hour, minute):
    result = parse(f"星期二{clock}帶Coco去公園", now=NOW)
    assert result.intent_type == IntentType.CREATE_EVENT
    assert result.start == datetime(2026, 10, 6, hour, minute, tzinfo=TZ)


@pytest.mark.parametrize("clock", ["0pm", "13am", "19:15pm", "a9pm", "109pm", "9pmx"])
def test_malformed_clock_does_not_create(clock):
    result = parse(f"星期二{clock}，去公園", now=NOW)
    assert result.intent_type != IntentType.CREATE_EVENT


def payload(**overrides):
    return {
        "intent_type": "create_event",
        "title": "影相+散步",
        "start": "2026-10-06T21:00:00+08:00",
        "end": None,
        "all_day": False,
        "location": None,
        "participants": ["Coco"],
        "missing_fields": [],
        "confidence": "medium",
    } | overrides


def provider(monkeypatch, response_payload):
    create = Mock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(response_payload))
                )
            ],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=60),
        )
    )
    monkeypatch.setattr(
        "openai.OpenAI",
        Mock(
            return_value=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            )
        ),
    )
    return XaiChatLlmParser(api_key="fake-key", model="fake-model"), create


def test_independent_request_and_compound_result(monkeypatch):
    llm, create = provider(monkeypatch, payload())
    result = parse_with_fallback(MESSAGE, now=NOW, llm=llm)
    request = create.call_args.kwargs
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True
    user = json.loads(request["messages"][1]["content"])
    assert user == {"now": NOW.isoformat(), "message": MESSAGE}
    system = request["messages"][0]["content"]
    assert "Activities\nare unrestricted" in system
    assert "missing_fields" in system
    assert result.title == "影相+散步"
    assert result.start == datetime(2026, 10, 6, 21, tzinfo=TZ)
    assert result.participants == ["Coco"]
    assert result.missing_fields == []
    assert result.raw_text == MESSAGE


@pytest.mark.parametrize(
    ("message", "fields"),
    [
        ("星期二Coco影相", {"start": None, "missing_fields": ["start"]}),
        ("加活動，9pm Coco影相", {"start": None, "missing_fields": ["start"]}),
        ("加活動，星期二9pm", {"title": None, "missing_fields": ["title"]}),
    ],
)
def test_real_missing_details_remain_partial(monkeypatch, message, fields):
    logger = Mock()
    monkeypatch.setattr("cec_vivisystem.parse_fallback.logger", logger)
    llm, _ = provider(monkeypatch, payload(intent_type="needs_clarification", **fields))
    result = parse_with_fallback(message, now=NOW, llm=llm)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert result.missing_fields == fields["missing_fields"]
    completion = logger.info.call_args_list[-1]
    assert completion.args == ("parse_fallback_succeeded",)
    assert completion.kwargs["outcome"] == "partial"
    assert completion.kwargs["prompt_version"] == "create_event.v4.txt"
    assert len(completion.kwargs["prompt_hash"]) == 64


def test_bad_provider_object_keeps_rule_result(monkeypatch):
    llm, _ = provider(monkeypatch, ["not an object"])
    result = parse_with_fallback(MESSAGE, now=NOW, llm=llm)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert result.title is None


def test_fallback_proposal_still_requires_yes(monkeypatch):
    llm, _ = provider(monkeypatch, payload())
    store = InMemoryConfirmationStore()
    calendar = FakeCalendarClient()
    result = process_slack_message_event(
        {
            "type": "message",
            "text": MESSAGE,
            "channel": "C_TEST",
            "user": "U_TEST",
            "ts": "1791180000.000001",
            "client_msg_id": "synthetic-create-23",
        },
        allowed_channel_ids={"C_TEST"},
        confirmation_store=store,
        calendar_client=calendar,
        llm_parser=llm,
        now=NOW,
    )
    assert result.parse_result.intent_type == IntentType.CREATE_EVENT
    assert "please confirm" in result.reply_text.lower()
    assert "21:00" in result.reply_text and "22:00" in result.reply_text
    assert len(store.list_pending()) == 1
    assert calendar.calls == []
